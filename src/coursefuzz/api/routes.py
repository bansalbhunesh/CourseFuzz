from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status
from fastapi.responses import FileResponse, StreamingResponse

from coursefuzz.data.demo import get_assignment
from coursefuzz.domain.models import (
    ApplyRequest,
    ApprovalReceipt,
    ApprovalRequest,
    RunCreate,
    RunStatus,
    RunView,
)
from coursefuzz.services.run_service import RunService

TERMINAL_STREAM_STATES = {
    RunStatus.APPROVAL_REQUIRED,
    RunStatus.APPROVED,
    RunStatus.VERIFIED,
    RunStatus.FAILED,
}


def build_router(service: RunService) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "truth_engine": "execution-backed", "mode": service.mode}

    @router.get("/demo")
    def demo() -> dict:
        assignment = get_assignment("triangle-classifier")
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

    @router.post("/runs", response_model=RunView, status_code=status.HTTP_202_ACCEPTED)
    def create_run(
        payload: RunCreate,
        background_tasks: BackgroundTasks,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> RunView:
        try:
            run, created = service.create_run(
                payload.assignment_id, idempotency_key or f"auto-{uuid4().hex}"
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if created:
            background_tasks.add_task(service.analyze_run, run.id)
        return run

    @router.get("/runs/{run_id}", response_model=RunView)
    def get_run(run_id: str) -> RunView:
        try:
            return service.require_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc

    @router.post("/runs/{run_id}/approval", response_model=ApprovalReceipt)
    def approve(run_id: str, payload: ApprovalRequest) -> ApprovalReceipt:
        try:
            return service.approve(run_id, payload.payload_sha256)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/runs/{run_id}/apply", response_model=RunView)
    def apply(run_id: str, payload: ApplyRequest) -> RunView:
        try:
            return service.apply(run_id, payload.approval_token)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.get("/runs/{run_id}/artifact")
    def artifact(run_id: str) -> FileResponse:
        record = service.repository.artifact(run_id)
        if not record:
            raise HTTPException(status_code=404, detail="Verified artifact not found")
        path, sha256 = record
        return FileResponse(
            path,
            media_type="text/x-python",
            filename=path.name,
            headers={"ETag": f'"{sha256}"', "X-Artifact-SHA256": sha256},
        )

    @router.get("/runs/{run_id}/events")
    async def events(
        run_id: str,
        request: Request,
        last_event_id: int | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        try:
            service.require_run(run_id)
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
                run = service.require_run(run_id)
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
