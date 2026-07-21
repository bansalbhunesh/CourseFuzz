from __future__ import annotations

import asyncio
import json
import os
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
from fastapi.responses import StreamingResponse
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
from coursefuzz.services.assignment_service import AssignmentService
from coursefuzz.services.run_service import RunService

TERMINAL_STREAM_STATES = {
    RunStatus.APPROVAL_REQUIRED,
    RunStatus.APPROVED,
    RunStatus.VERIFIED,
    RunStatus.NO_ACTION_REQUIRED,
    RunStatus.FAILED,
}


class SessionCreate(BaseModel):
    access_token: str = Field(min_length=1, max_length=512)


def build_router(
    service: RunService,
    assignments: AssignmentService,
    access: AccessPolicy,
) -> APIRouter:
    router = APIRouter(prefix="/api")

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
        principal: Principal = principal_dependency,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> RunView:
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
        if created:
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

    return router
