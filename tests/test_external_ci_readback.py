"""Tests for automatic target-repository CI read-back.

A GitHub action is only fully verified once byte read-back AND the destination repository's own CI
conclude. These tests drive that with a mock GitHub transport: the write holds the run at
external_ci_pending, and polling the target check-runs advances it to verified, external_ci_failed,
or a timeout — never merging the PR — and resumes through the recovery loop.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from coursefuzz.adapters.destinations import (
    DestinationCoordinator,
    GitHubDestinationAdapter,
)
from coursefuzz.adapters.hypotheses import DeterministicHypothesisProvider
from coursefuzz.adapters.sandbox import LocalRestrictedRunner
from coursefuzz.data.demo import TRIANGLE_ASSIGNMENT
from coursefuzz.domain.engine import AssessmentEngine
from coursefuzz.domain.models import ActionReceipt, GitHubPullRequestDestination, RunStatus
from coursefuzz.repositories.sqlite import RunRepository
from coursefuzz.services.assignment_service import AssignmentService
from coursefuzz.services.run_service import RunService

REPO = "course-owner/autograder"
STARTED = datetime(2026, 1, 1, tzinfo=UTC)


def _github_service(
    tmp_path: Path, handler: Callable[[httpx.Request], httpx.Response]
) -> RunService:
    repository = RunRepository(tmp_path / "coursefuzz.db")
    sandbox = LocalRestrictedRunner()
    assignments = AssignmentService(repository, sandbox)
    assignments.seed(
        TRIANGLE_ASSIGNMENT.model_copy(
            update={
                "id": "triangle-github",
                "destination": GitHubPullRequestDestination(
                    repository=REPO, base_branch="main", test_directory="tests/coursefuzz"
                ),
            }
        )
    )
    client = httpx.Client(
        base_url="https://api.github.test", transport=httpx.MockTransport(handler)
    )
    github = GitHubDestinationAdapter(client=client, allowed_repositories={REPO})
    coordinator = DestinationCoordinator(artifact_dir=tmp_path / "artifacts", github=github)
    engine = AssessmentEngine(sandbox, DeterministicHypothesisProvider())
    return RunService(
        repository,
        engine,
        assignments,
        tmp_path / "artifacts",
        "deterministic-fallback",
        coordinator,
    )


def _apply_handler() -> Callable[[httpx.Request], httpx.Response]:
    """Content-agnostic happy-path handler: echoes back whatever bytes were written."""

    state: dict[str, str | None] = {"content": None}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and "/git/ref/heads/" in path:
            return httpx.Response(200, json={"object": {"sha": "a" * 40}})
        if request.method == "POST" and path.endswith("/git/refs"):
            return httpx.Response(201, json={"ref": json.loads(request.content)["ref"]})
        if request.method == "GET" and "/contents/" in path:
            if state["content"] is None:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"sha": "blob", "content": state["content"]})
        if request.method == "PUT" and "/contents/" in path:
            state["content"] = json.loads(request.content)["content"]
            return httpx.Response(201, json={"commit": {"sha": "b" * 40}})
        if request.method == "POST" and path.endswith("/pulls"):
            return httpx.Response(
                201, json={"number": 7, "html_url": "https://github.test/x/pull/7"}
            )
        raise AssertionError(f"Unexpected request: {request.method} {path}")

    return handler


def _check_runs_handler(payload: dict) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/check-runs" in request.url.path:
            return httpx.Response(200, json=payload)
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    return handler


def _make_pending_run(service: RunService) -> str:
    run, _ = service.create_run("triangle-github", "pending-run")
    receipt = ActionReceipt(
        kind="github_pull_request",
        path="tests/coursefuzz/test_x.py",
        artifact_sha256="a" * 64,
        read_back_verified=True,
        repository=REPO,
        commit_sha="b" * 40,
        external_url="https://github.test/x/pull/7",
        external_ci_started_at=STARTED,
    )
    service.repository.save(
        run.model_copy(
            update={"status": RunStatus.EXTERNAL_CI_PENDING, "action_receipt": receipt}
        )
    )
    return run.id


_RUN_URL = "https://github.test/x/runs/1"
_COMPLETED_SUCCESS = {
    "check_runs": [{"status": "completed", "conclusion": "success", "html_url": _RUN_URL}]
}
_COMPLETED_FAILURE = {
    "check_runs": [{"status": "completed", "conclusion": "failure", "html_url": _RUN_URL}]
}
_IN_PROGRESS = {
    "check_runs": [{"status": "in_progress", "conclusion": None, "html_url": _RUN_URL}]
}


def test_github_apply_holds_at_external_ci_pending(tmp_path: Path) -> None:
    service = _github_service(tmp_path, _apply_handler())
    run, _ = service.create_run("triangle-github", "gh-apply")
    service.analyze_run(run.id)
    analyzed = service.require_run(run.id)
    assert analyzed.status == RunStatus.APPROVAL_REQUIRED
    assert analyzed.analysis and analyzed.analysis.candidate
    receipt = service.approve(run.id, analyzed.analysis.candidate.payload_sha256)

    result = service.apply(run.id, receipt.approval_token)

    # The write and byte read-back succeeded, but the run is NOT verified yet.
    assert result.status == RunStatus.EXTERNAL_CI_PENDING
    action = result.action_receipt
    assert action is not None
    assert action.read_back_verified is True
    assert action.commit_sha == "b" * 40
    assert action.external_url == "https://github.test/x/pull/7"
    assert action.external_ci_started_at is not None
    assert action.external_ci_verified is False


def test_poll_verifies_when_target_ci_passes(tmp_path: Path) -> None:
    service = _github_service(tmp_path, _check_runs_handler(_COMPLETED_SUCCESS))
    run_id = _make_pending_run(service)

    result = service.poll_external_ci(run_id, now=lambda: STARTED + timedelta(seconds=30))

    assert result.status == RunStatus.VERIFIED
    assert result.action_receipt.external_ci_verified is True
    assert result.action_receipt.external_ci_conclusion == "success"
    assert result.action_receipt.external_ci_completed_at is not None


def test_poll_fails_when_target_ci_fails(tmp_path: Path) -> None:
    service = _github_service(tmp_path, _check_runs_handler(_COMPLETED_FAILURE))
    run_id = _make_pending_run(service)

    result = service.poll_external_ci(run_id, now=lambda: STARTED + timedelta(seconds=30))

    assert result.status == RunStatus.EXTERNAL_CI_FAILED
    assert result.action_receipt.external_ci_verified is False
    assert result.action_receipt.external_ci_conclusion == "failure"
    assert "failure" in (result.error or "")


def test_poll_stays_pending_then_times_out(tmp_path: Path) -> None:
    service = _github_service(tmp_path, _check_runs_handler(_IN_PROGRESS))
    run_id = _make_pending_run(service)

    # Within the deadline and still running: unchanged, still pending.
    still = service.poll_external_ci(
        run_id, deadline_seconds=600, now=lambda: STARTED + timedelta(seconds=10)
    )
    assert still.status == RunStatus.EXTERNAL_CI_PENDING

    # Past the deadline with no conclusion: a visible timeout, not a silent hang.
    timed_out = service.poll_external_ci(
        run_id, deadline_seconds=600, now=lambda: STARTED + timedelta(seconds=1000)
    )
    assert timed_out.status == RunStatus.EXTERNAL_CI_FAILED
    assert timed_out.action_receipt.external_ci_conclusion == "timed_out"


def test_recovery_resumes_external_ci_polling(tmp_path: Path) -> None:
    # A process restart: recover_incomplete_runs must pick the pending run back up.
    service = _github_service(tmp_path, _check_runs_handler(_COMPLETED_SUCCESS))
    run_id = _make_pending_run(service)

    recovered = service.recover_incomplete_runs()

    assert recovered >= 1
    assert service.require_run(run_id).status == RunStatus.VERIFIED


def test_check_runs_aggregation() -> None:
    def adapter(payload: dict) -> GitHubDestinationAdapter:
        client = httpx.Client(
            base_url="https://api.github.test",
            transport=httpx.MockTransport(lambda _req: httpx.Response(200, json=payload)),
        )
        return GitHubDestinationAdapter(client=client, allowed_repositories={REPO})

    assert adapter({"check_runs": []}).check_runs(REPO, "abc").state == "pending"
    assert adapter(_IN_PROGRESS).check_runs(REPO, "abc").state == "pending"
    assert adapter(_COMPLETED_SUCCESS).check_runs(REPO, "abc").state == "success"
    failing = adapter(_COMPLETED_FAILURE).check_runs(REPO, "abc")
    assert failing.state == "failure"
    assert failing.conclusion == "failure"
