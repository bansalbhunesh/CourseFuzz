from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from collections.abc import AsyncIterator
from uuid import uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from coursefuzz.domain.models import (
    ApplyRequest,
    ApprovalReceipt,
    ApprovalRequest,
    AssignmentCreate,
    AssignmentSnapshot,
    AssignmentSummary,
    RunCreate,
    RunStatus,
    RunView,
)
from coursefuzz.security.access import SESSION_COOKIE, AccessPolicy, Principal
from coursefuzz.security.github_oauth import GitHubOAuthClient, sign_state, verify_state
from coursefuzz.security.installations import InstallationStore, apply_installation_event
from coursefuzz.security.rate_limit import TokenBucketRateLimiter
from coursefuzz.security.webhooks import (
    DELIVERY_HEADER,
    EVENT_HEADER,
    SIGNATURE_HEADER,
    parse_installation_event,
    verify_github_signature,
)
from coursefuzz.services.assignment_service import AssignmentService
from coursefuzz.services.run_service import RunService

TERMINAL_STREAM_STATES = {
    RunStatus.APPROVAL_REQUIRED,
    RunStatus.APPROVED,
    RunStatus.VERIFIED,
    RunStatus.NO_ACTION_REQUIRED,
    RunStatus.EXTERNAL_CI_FAILED,
    RunStatus.FAILED,
}


class SessionCreate(BaseModel):
    access_token: str = Field(min_length=1, max_length=512)


class InstallationClaim(BaseModel):
    installation_id: int = Field(gt=0)


def build_router(
    service: RunService,
    assignments: AssignmentService,
    access: AccessPolicy,
    installation_store: InstallationStore | None = None,
    oauth_client: GitHubOAuthClient | None = None,
    rate_limiter: TokenBucketRateLimiter | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api")
    webhook_secret = os.getenv("COURSEFUZZ_GITHUB_WEBHOOK_SECRET", "").strip()
    self_serve_claim_enabled = os.getenv("COURSEFUZZ_ENABLE_SELF_SERVE_CLAIM", "0") == "1"
    oauth_redirect_uri = os.getenv("COURSEFUZZ_GITHUB_OAUTH_REDIRECT_URI", "").strip()

    def _callback_redirect_uri(request: Request) -> str:
        if oauth_redirect_uri:
            return oauth_redirect_uri
        return f"{str(request.base_url).rstrip('/')}/api/github/callback"

    def current_principal(request: Request) -> Principal:
        try:
            return access.authenticate(
                request.headers.get("Authorization"),
                request.cookies.get(SESSION_COOKIE),
            )
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

    principal_dependency = Depends(current_principal)

    @router.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "truth_engine": "execution-backed",
            "mode": service.mode,
            "github_destination": (
                "configured" if service.github_destination_available else "unconfigured"
            ),
            "github_auth": service.github_destination_auth_mode,
            "auth": access.mode,
            "storage": service.repository.backend_name,
            "commit": os.getenv(
                "COURSEFUZZ_COMMIT_SHA",
                os.getenv("RENDER_GIT_COMMIT", "local"),
            ),
        }

    @router.post("/session")
    def create_session(payload: SessionCreate, response: Response) -> dict[str, str]:
        try:
            principal = access.authenticate(f"Bearer {payload.access_token}")
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid CourseFuzz credential",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        response.set_cookie(
            SESSION_COOKIE,
            payload.access_token,
            httponly=True,
            secure=os.getenv("COURSEFUZZ_COOKIE_SECURE", "1") != "0",
            samesite="strict",
            max_age=8 * 60 * 60,
            path="/",
        )
        return {"tenant_id": principal.tenant_id}

    @router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
    def delete_session(response: Response) -> None:
        response.delete_cookie(SESSION_COOKIE, path="/")

    @router.get("/session")
    def get_session(
        principal: Principal = principal_dependency,
    ) -> dict[str, str]:
        return {"tenant_id": principal.tenant_id}

    @router.get("/demo")
    def demo(principal: Principal = principal_dependency) -> dict:
        assignment = assignments.require("triangle-classifier", principal.tenant_id).spec
        return {
            "id": assignment.id,
            "title": assignment.title,
            "summary": assignment.summary,
            "language": assignment.language,
            "entrypoint": assignment.entrypoint,
            "instructor_tests": [
                test.model_dump(mode="json") for test in assignment.instructor_tests
            ],
            "mutant_count": len(assignment.mutants),
            "accepted_solution_count": len(assignment.accepted_solutions),
            "mode": service.mode,
        }

    @router.get("/assignments", response_model=list[AssignmentSummary])
    def list_assignments(
        principal: Principal = principal_dependency,
    ) -> list[AssignmentSummary]:
        return assignments.list(principal.tenant_id)

    @router.post(
        "/assignments",
        response_model=AssignmentSnapshot,
        status_code=status.HTTP_201_CREATED,
    )
    def create_assignment(
        payload: AssignmentCreate,
        response: Response,
        principal: Principal = principal_dependency,
    ) -> AssignmentSnapshot:
        try:
            snapshot, created = assignments.create(payload, principal.tenant_id)
            if not created:
                response.status_code = status.HTTP_200_OK
            return snapshot
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/assignments/{assignment_id}", response_model=AssignmentSnapshot)
    def get_assignment(
        assignment_id: str,
        principal: Principal = principal_dependency,
    ) -> AssignmentSnapshot:
        try:
            return assignments.require(assignment_id, principal.tenant_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Assignment not found") from exc

    @router.post("/runs", response_model=RunView, status_code=status.HTTP_202_ACCEPTED)
    def create_run(
        payload: RunCreate,
        background_tasks: BackgroundTasks,
        response: Response,
        principal: Principal = principal_dependency,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> RunView:
        if rate_limiter and rate_limiter.enabled and not rate_limiter.allow(principal.tenant_id):
            retry = rate_limiter.retry_after_seconds(principal.tenant_id)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Retry after {retry}s.",
                headers={"Retry-After": str(retry)},
            )
        try:
            run, created = service.create_run(
                payload.assignment_id,
                idempotency_key or f"auto-{uuid4().hex}",
                principal.tenant_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        # When a separate execution worker is deployed, defer analysis to it: the run stays
        # queued and a worker claims it. Default keeps the API's inline analysis for the demo.
        if created and os.getenv("COURSEFUZZ_DEFER_ANALYSIS") != "1":
            background_tasks.add_task(service.analyze_run, run.id, principal.tenant_id)
        return run

    @router.get("/runs", response_model=list[RunView])
    def list_runs(
        principal: Principal = principal_dependency,
        assignment_id: str | None = None,
    ) -> list[RunView]:
        return service.list_runs(assignment_id, principal.tenant_id)

    @router.get("/runs/{run_id}", response_model=RunView)
    def get_run(
        run_id: str,
        principal: Principal = principal_dependency,
    ) -> RunView:
        try:
            return service.require_run(run_id, principal.tenant_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc

    @router.post("/runs/{run_id}/approval", response_model=ApprovalReceipt)
    def approve(
        run_id: str,
        payload: ApprovalRequest,
        principal: Principal = principal_dependency,
    ) -> ApprovalReceipt:
        try:
            return service.approve(run_id, payload.payload_sha256, principal.tenant_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/runs/{run_id}/apply", response_model=RunView)
    def apply(
        run_id: str,
        payload: ApplyRequest,
        principal: Principal = principal_dependency,
    ) -> RunView:
        try:
            return service.apply(run_id, payload.approval_token, principal.tenant_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.post("/runs/{run_id}/external-ci", response_model=RunView)
    def refresh_external_ci(
        run_id: str,
        principal: Principal = principal_dependency,
    ) -> RunView:
        # Advance an external_ci_pending run by reading the target CI once. A worker (or startup
        # recovery) also does this automatically; this endpoint lets the app refresh on demand.
        try:
            return service.poll_external_ci(run_id, principal.tenant_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc

    @router.get("/runs/{run_id}/artifact")
    def artifact(
        run_id: str,
        principal: Principal = principal_dependency,
    ) -> Response:
        try:
            service.require_run(run_id, principal.tenant_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Verified artifact not found") from exc
        record = service.repository.artifact(run_id)
        if not record:
            raise HTTPException(status_code=404, detail="Verified artifact not found")
        return Response(
            content=record.content,
            media_type="text/x-python",
            headers={
                "Content-Disposition": f'attachment; filename="{record.filename}"',
                "ETag": f'"{record.sha256}"',
                "X-Artifact-SHA256": record.sha256,
            },
        )

    @router.get("/runs/{run_id}/evidence")
    def evidence_bundle(
        run_id: str,
        principal: Principal = principal_dependency,
    ) -> Response:
        # A downloadable, independently re-hashable record of the run: assignment snapshot,
        # oracle provenance, approval, destination read-back receipt, and the ordered audit trail.
        try:
            bundle = service.build_evidence_bundle(run_id, principal.tenant_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        return Response(
            content=bundle.model_dump_json(indent=2),
            media_type="application/json",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="coursefuzz-evidence-{run_id}.json"'
                ),
                "X-Evidence-SHA256": bundle.bundle_sha256,
            },
        )

    @router.get("/runs/{run_id}/events")
    async def events(
        run_id: str,
        request: Request,
        principal: Principal = principal_dependency,
        last_event_id: int | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        try:
            service.require_run(run_id, principal.tenant_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc

        async def stream() -> AsyncIterator[str]:
            cursor = last_event_id or 0
            idle_ticks = 0
            while not await request.is_disconnected():
                new_events = service.repository.events_after(run_id, cursor)
                for event in new_events:
                    cursor = event.id
                    body = event.model_dump(mode="json")
                    yield f"id: {event.id}\nevent: {event.event_type}\ndata: {json.dumps(body)}\n\n"
                run = service.require_run(run_id, principal.tenant_id)
                if run.status in TERMINAL_STREAM_STATES and not new_events:
                    yield f"event: stream.paused\ndata: {json.dumps({'status': run.status})}\n\n"
                    break
                idle_ticks += 1
                if idle_ticks >= 30:
                    yield ": keep-alive\n\n"
                    idle_ticks = 0
                await asyncio.sleep(0.1)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/github/webhook", status_code=status.HTTP_202_ACCEPTED)
    async def github_webhook(request: Request) -> dict[str, str]:
        """Ingest signed GitHub App installation callbacks into the durable store.

        Fail-closed: unconfigured secret/store -> 503, bad signature -> 401. Deliveries are
        deduplicated by ``X-GitHub-Delivery`` so a redelivery never applies twice, and only the
        installation lifecycle events we recognize mutate the store.
        """

        if installation_store is None or not webhook_secret:
            raise HTTPException(status_code=503, detail="GitHub webhooks are not configured")
        body = await request.body()
        if not verify_github_signature(webhook_secret, body, request.headers.get(SIGNATURE_HEADER)):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
        delivery_id = request.headers.get(DELIVERY_HEADER)
        if not delivery_id:
            raise HTTPException(status_code=400, detail="Missing delivery identifier")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Malformed webhook body") from exc
        first_delivery = await asyncio.to_thread(installation_store.record_delivery, delivery_id)
        if not first_delivery:
            return {"status": "duplicate"}
        event = parse_installation_event(request.headers.get(EVENT_HEADER, ""), payload)
        if event is None:
            return {"status": "ignored"}
        await asyncio.to_thread(apply_installation_event, installation_store, event)
        return {"status": "applied", "action": event.action}

    @router.get("/github/repositories")
    def github_repositories(principal: Principal = principal_dependency) -> dict:
        """List the repositories the caller's workspace onboarded (repository picker data)."""

        if installation_store is None:
            return {"installation_id": None, "repositories": []}
        installation_id = installation_store.installation_for_workspace(principal.tenant_id)
        return {
            "installation_id": installation_id,
            "repositories": installation_store.repositories_for_workspace(principal.tenant_id),
        }

    @router.post("/github/installations/claim")
    def github_claim(
        payload: InstallationClaim,
        principal: Principal = principal_dependency,
    ) -> dict:
        """Bind the authenticated workspace to a GitHub App installation (first-claim-wins).

        Disabled by default: binding by raw installation ID is only safe once the caller's GitHub
        identity is verified (OAuth), so production keeps this off until that check ships. When
        enabled for controlled environments, an installation already owned by another workspace
        cannot be re-claimed.
        """

        if installation_store is None or not self_serve_claim_enabled:
            raise HTTPException(status_code=404, detail="Self-serve installation claim is disabled")
        claimed = installation_store.claim_installation(
            principal.tenant_id, payload.installation_id
        )
        if not claimed:
            raise HTTPException(
                status_code=409,
                detail="Installation is unknown, suspended, or already bound to another workspace",
            )
        return {
            "installation_id": payload.installation_id,
            "repositories": installation_store.repositories_for_workspace(principal.tenant_id),
        }

    @router.get("/github/login")
    def github_login(
        installation_id: int,
        request: Request,
        principal: Principal = principal_dependency,
    ) -> RedirectResponse:
        """Begin GitHub OAuth to verify the caller owns ``installation_id`` before binding it.

        The tenant and target installation are carried in a signed ``state`` (HMAC over the OAuth
        client secret) so the callback — which the SameSite-strict session cookie cannot reach after
        a redirect from github.com — can still trust who initiated the flow.
        """

        if oauth_client is None:
            raise HTTPException(status_code=503, detail="GitHub OAuth is not configured")
        state = sign_state(
            {
                "tenant_id": principal.tenant_id,
                "installation_id": installation_id,
                "nonce": secrets.token_urlsafe(16),
                "iat": int(time.time()),
            },
            oauth_client.state_secret,
        )
        target = oauth_client.authorize_url(
            state=state, redirect_uri=_callback_redirect_uri(request)
        )
        return RedirectResponse(target, status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    @router.get("/github/callback")
    def github_callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
        """Complete GitHub OAuth: bind the workspace only if the user owns the installation.

        The binding is safe without a feature flag because ownership is proven — the installation ID
        from the signed state must appear in the user's own ``/user/installations`` list.
        """

        if oauth_client is None or installation_store is None:
            raise HTTPException(status_code=503, detail="GitHub OAuth is not configured")
        payload = verify_state(state, oauth_client.state_secret)
        if payload is None or not code:
            return RedirectResponse("/?github=error", status_code=status.HTTP_303_SEE_OTHER)
        tenant_id = payload.get("tenant_id")
        installation_id = payload.get("installation_id")
        if not isinstance(tenant_id, str) or not isinstance(installation_id, int):
            return RedirectResponse("/?github=error", status_code=status.HTTP_303_SEE_OTHER)
        try:
            user_token = oauth_client.exchange_code(
                code=code, redirect_uri=_callback_redirect_uri(request)
            )
            owned = oauth_client.user_installation_ids(user_token)
        except RuntimeError:
            return RedirectResponse("/?github=error", status_code=status.HTTP_303_SEE_OTHER)
        if installation_id not in owned or not installation_store.installation_exists(
            installation_id
        ):
            return RedirectResponse("/?github=denied", status_code=status.HTTP_303_SEE_OTHER)
        if not installation_store.claim_installation(tenant_id, installation_id):
            return RedirectResponse("/?github=conflict", status_code=status.HTTP_303_SEE_OTHER)
        return RedirectResponse("/?github=connected", status_code=status.HTTP_303_SEE_OTHER)

    return router
