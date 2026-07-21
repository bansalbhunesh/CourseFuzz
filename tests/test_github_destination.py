import base64
import json
from pathlib import Path

import httpx
import pytest

from coursefuzz.adapters.destinations import (
    DestinationCoordinator,
    GitHubDestinationAdapter,
)
from coursefuzz.adapters.hypotheses import DeterministicHypothesisProvider
from coursefuzz.adapters.sandbox import SubprocessPythonSandbox
from coursefuzz.data.demo import TRIANGLE_ASSIGNMENT
from coursefuzz.domain.engine import AssessmentEngine
from coursefuzz.domain.models import (
    CandidatePatch,
    GitHubPullRequestDestination,
    PatchTarget,
)
from coursefuzz.domain.models import TestCase as CFTestCase


def test_github_destination_binds_base_commit_writes_pr_and_reads_back() -> None:
    assignment = TRIANGLE_ASSIGNMENT.model_copy(
        update={
            "destination": GitHubPullRequestDestination(
                repository="course-owner/autograder",
                base_branch="main",
                test_directory="tests/coursefuzz",
            )
        }
    )
    analysis = AssessmentEngine(
        SubprocessPythonSandbox(), DeterministicHypothesisProvider()
    ).analyze(assignment)
    assert analysis.candidate is not None
    unbound = analysis.candidate
    calls: list[tuple[str, str]] = []
    content_reads = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal content_reads
        calls.append((request.method, request.url.path))
        if request.method == "GET" and "/git/ref/heads/" in request.url.path:
            return httpx.Response(200, json={"object": {"sha": "a" * 40}})
        if request.method == "POST" and request.url.path.endswith("/git/refs"):
            payload = json.loads(request.content)
            assert payload["sha"] == "a" * 40
            assert payload["ref"].startswith("refs/heads/coursefuzz/run_test-")
            return httpx.Response(201, json={"ref": payload["ref"]})
        if request.method == "GET" and "/contents/" in request.url.path:
            content_reads += 1
            if content_reads == 1:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(
                200,
                json={
                    "sha": "blob-sha",
                    "content": base64.b64encode(unbound.pytest_source.encode()).decode(),
                },
            )
        if request.method == "PUT" and "/contents/" in request.url.path:
            payload = json.loads(request.content)
            assert base64.b64decode(payload["content"]).decode() == unbound.pytest_source
            return httpx.Response(201, json={"commit": {"sha": "b" * 40}})
        if request.method == "POST" and request.url.path.endswith("/pulls"):
            payload = json.loads(request.content)
            assert payload["draft"] is True
            assert payload["base"] == "main"
            return httpx.Response(
                201,
                json={
                    "number": 17,
                    "html_url": "https://github.test/course-owner/autograder/pull/17",
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = httpx.Client(
        base_url="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )
    adapter = GitHubDestinationAdapter(
        client=client,
        allowed_repositories={"course-owner/autograder"},
    )

    prepared = adapter.prepare("run_test", unbound)
    applied = adapter.apply(prepared)

    assert prepared.target.base_commit_sha == "a" * 40
    assert prepared.target.head_branch is not None
    assert prepared.payload_sha256 != unbound.payload_sha256
    assert applied.receipt.read_back_verified is True
    assert applied.receipt.pull_request_number == 17
    assert applied.receipt.commit_sha == "b" * 40
    assert calls[-1][0] == "GET"


def test_github_destination_fails_closed_outside_repository_allowlist() -> None:
    assignment = TRIANGLE_ASSIGNMENT.model_copy(
        update={
            "destination": GitHubPullRequestDestination(
                repository="course-owner/autograder",
                base_branch="main",
            )
        }
    )
    candidate = AssessmentEngine(
        SubprocessPythonSandbox(), DeterministicHypothesisProvider()
    ).analyze(assignment).candidate
    assert candidate is not None
    client = httpx.Client(
        base_url="https://api.github.test",
        transport=httpx.MockTransport(lambda _: httpx.Response(500)),
    )
    adapter = GitHubDestinationAdapter(
        client=client,
        allowed_repositories={"course-owner/dedicated-demo-target"},
    )

    try:
        adapter.prepare("run_test", candidate)
    except RuntimeError as exc:
        assert "not in COURSEFUZZ_GITHUB_ALLOWED_REPOS" in str(exc)
    else:
        raise AssertionError("GitHub destination accepted a repository outside its allowlist")


def test_github_destination_fails_closed_when_read_back_bytes_are_tampered() -> None:
    """The green receipt must be earned: if the destination returns bytes that differ from the
    approved payload on the final read-back, apply() raises and returns no receipt. This guards
    the artifact-closure claim that a success means the exact bytes are present, not merely that a
    PR was opened.
    """

    assignment = TRIANGLE_ASSIGNMENT.model_copy(
        update={
            "destination": GitHubPullRequestDestination(
                repository="course-owner/autograder",
                base_branch="main",
                test_directory="tests/coursefuzz",
            )
        }
    )
    unbound = AssessmentEngine(
        SubprocessPythonSandbox(), DeterministicHypothesisProvider()
    ).analyze(assignment).candidate
    assert unbound is not None
    content_reads = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal content_reads
        if request.method == "GET" and "/git/ref/heads/" in request.url.path:
            return httpx.Response(200, json={"object": {"sha": "a" * 40}})
        if request.method == "POST" and request.url.path.endswith("/git/refs"):
            return httpx.Response(201, json={"ref": "refs/heads/coursefuzz/run"})
        if request.method == "GET" and "/contents/" in request.url.path:
            content_reads += 1
            if content_reads == 1:
                return httpx.Response(404, json={"message": "Not Found"})
            # Final read-back returns bytes that do NOT match the approved payload.
            tampered = b"def test_tampered():\n    assert False\n"
            return httpx.Response(
                200,
                json={"sha": "blob-sha", "content": base64.b64encode(tampered).decode()},
            )
        if request.method == "PUT" and "/contents/" in request.url.path:
            return httpx.Response(201, json={"commit": {"sha": "b" * 40}})
        if request.method == "POST" and request.url.path.endswith("/pulls"):
            return httpx.Response(
                201,
                json={"number": 17, "html_url": "https://github.test/x/pull/17"},
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = httpx.Client(
        base_url="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )
    adapter = GitHubDestinationAdapter(
        client=client,
        allowed_repositories={"course-owner/autograder"},
    )

    prepared = adapter.prepare("run_test", unbound)
    with pytest.raises(RuntimeError, match="read-back did not match the approved payload"):
        adapter.apply(prepared)


def test_local_artifact_write_cannot_escape_the_run_directory(tmp_path: Path) -> None:
    """Defense in depth: even if a patch target carries a traversing path, the local writer must
    refuse it before creating or writing any file outside the bounded run directory.
    """

    malicious = CandidatePatch(
        id="patch-escape",
        test=CFTestCase(inputs=(1,), expected=1, label="probe", source="minimized"),
        rationale="attempts to escape the run directory",
        target_mutants=("mutant-x",),
        payload_sha256="0" * 64,
        pytest_source="def test_escape():\n    assert True\n",
        target=PatchTarget(kind="local_artifact", path="../../escape.py"),
    )
    coordinator = DestinationCoordinator(artifact_dir=tmp_path)

    with pytest.raises(RuntimeError, match="escaped the bounded run directory"):
        coordinator.apply("run_1", malicious)

    assert not (tmp_path.parent / "escape.py").exists()
