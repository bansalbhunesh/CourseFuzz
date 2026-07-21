import base64
import json

import httpx

from coursefuzz.adapters.destinations import GitHubDestinationAdapter
from coursefuzz.adapters.hypotheses import DeterministicHypothesisProvider
from coursefuzz.adapters.sandbox import SubprocessPythonSandbox
from coursefuzz.data.demo import TRIANGLE_ASSIGNMENT
from coursefuzz.domain.engine import AssessmentEngine
from coursefuzz.domain.models import GitHubPullRequestDestination


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
