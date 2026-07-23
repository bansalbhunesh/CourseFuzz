from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request
from starlette.responses import Response

from coursefuzz.adapters.destinations import DestinationCoordinator, GitHubDestinationAdapter
from coursefuzz.adapters.hypotheses import build_hypothesis_provider
from coursefuzz.adapters.sandbox import SubprocessPythonSandbox
from coursefuzz.api.routes import build_router
from coursefuzz.config import analysis_deadline_seconds
from coursefuzz.data.demo import TRIANGLE_ASSIGNMENT, TRIANGLE_GITHUB_ASSIGNMENT
from coursefuzz.domain.engine import AssessmentEngine
from coursefuzz.repositories.postgres import PostgresRunRepository
from coursefuzz.repositories.sqlite import RunRepository
from coursefuzz.security.access import AccessPolicy
from coursefuzz.security.github_app import build_credential_provider
from coursefuzz.security.github_oauth import GitHubOAuthClient
from coursefuzz.security.installations import build_installation_store
from coursefuzz.security.rate_limit import TokenBucketRateLimiter
from coursefuzz.services.assignment_service import AssignmentService
from coursefuzz.services.run_service import RunService


def create_app(
    database_path: str | Path | None = None,
    artifact_dir: str | Path | None = None,
    destination_coordinator: DestinationCoordinator | None = None,
    access_policy: AccessPolicy | None = None,
) -> FastAPI:
    provider = build_hypothesis_provider()
    database_url = os.getenv("DATABASE_URL") if database_path is None else None
    db_path = database_path or os.getenv("COURSEFUZZ_DB_PATH", "coursefuzz.db")
    repository = PostgresRunRepository(database_url) if database_url else RunRepository(db_path)
    installation_store = build_installation_store(database_url, db_path)
    sandbox = SubprocessPythonSandbox()
    assignment_service = AssignmentService(repository, sandbox)
    assignment_service.seed(TRIANGLE_ASSIGNMENT)
    assignment_service.seed(TRIANGLE_GITHUB_ASSIGNMENT)
    engine = AssessmentEngine(
        sandbox,
        provider,
        max_analysis_seconds=analysis_deadline_seconds(),
    )
    artifact_directory = artifact_dir or os.getenv("COURSEFUZZ_ARTIFACT_DIR", "data/artifacts")
    if destination_coordinator is None:
        destination_coordinator = DestinationCoordinator(
            artifact_directory,
            github=GitHubDestinationAdapter(
                credential_provider=build_credential_provider(installation_store)
            ),
        )
    service = RunService(
        repository,
        engine,
        assignment_service,
        artifact_directory,
        provider.mode,
        destination_coordinator,
    )
    access = access_policy or AccessPolicy.from_env()
    rate_limiter = TokenBucketRateLimiter.from_env()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await asyncio.to_thread(service.recover_incomplete_runs)
        yield

    app = FastAPI(
        title="CourseFuzz",
        version="0.2.0",
        description="Execution-backed autograder red-team and repair system",
        lifespan=lifespan,
    )
    app.state.run_service = service
    app.state.assignment_service = assignment_service
    app.state.access_policy = access
    app.state.installation_store = installation_store
    app.state.rate_limiter = rate_limiter
    oauth_client = GitHubOAuthClient.from_env()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "Last-Event-ID",
        ],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next: object) -> Response:
        response = await call_next(request)  # type: ignore[operator]
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if os.getenv("COURSEFUZZ_COOKIE_SECURE", "1") != "0":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
            )
        return response

    app.include_router(
        build_router(
            service,
            assignment_service,
            access,
            installation_store,
            oauth_client,
            rate_limiter,
        )
    )

    default_web_dist = Path(__file__).resolve().parents[2] / "web" / "dist"
    web_dist = Path(os.getenv("COURSEFUZZ_WEB_DIST", default_web_dist)).resolve()
    if web_dist.exists():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
    return app


app = create_app()
