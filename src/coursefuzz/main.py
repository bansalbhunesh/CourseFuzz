from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from coursefuzz.adapters.hypotheses import build_hypothesis_provider
from coursefuzz.adapters.sandbox import SubprocessPythonSandbox
from coursefuzz.api.routes import build_router
from coursefuzz.domain.engine import AssessmentEngine
from coursefuzz.repositories.sqlite import RunRepository
from coursefuzz.services.run_service import RunService


def create_app(
    database_path: str | Path | None = None,
    artifact_dir: str | Path | None = None,
) -> FastAPI:
    provider = build_hypothesis_provider()
    repository = RunRepository(database_path or os.getenv("COURSEFUZZ_DB_PATH", "coursefuzz.db"))
    engine = AssessmentEngine(SubprocessPythonSandbox(), provider)
    service = RunService(
        repository,
        engine,
        artifact_dir or os.getenv("COURSEFUZZ_ARTIFACT_DIR", "data/artifacts"),
        provider.mode,
    )

    app = FastAPI(
        title="CourseFuzz",
        version="0.1.0",
        description="Execution-backed autograder red-team and repair system",
    )
    app.state.run_service = service
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Idempotency-Key", "Last-Event-ID"],
    )
    app.include_router(build_router(service))

    default_web_dist = Path(__file__).resolve().parents[2] / "web" / "dist"
    web_dist = Path(os.getenv("COURSEFUZZ_WEB_DIST", default_web_dist)).resolve()
    if web_dist.exists():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
    return app


app = create_app()
