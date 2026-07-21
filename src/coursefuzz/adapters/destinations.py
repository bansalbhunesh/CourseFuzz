from __future__ import annotations

import base64
import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import httpx

from coursefuzz.domain.engine import bind_candidate_payload
from coursefuzz.domain.models import ActionReceipt, CandidatePatch

GITHUB_API_VERSION = "2026-03-10"
RETRYABLE_STATUSES = {429, 502, 503, 504}
# GitHub check-run conclusions that count as the target suite passing.
PASSING_CONCLUSIONS = {"success", "neutral", "skipped"}


@dataclass(frozen=True)
class AppliedDestination:
    receipt: ActionReceipt
    local_path: Path | None = None


@dataclass(frozen=True)
class CheckRunStatus:
    """Aggregate result of a commit's GitHub check-runs."""

    state: Literal["pending", "success", "failure"]
    conclusion: str | None = None
    url: str | None = None


class GitHubDestinationAdapter:
    def __init__(
        self,
        token: str | None = None,
        client: httpx.Client | None = None,
        allowed_repositories: set[str] | None = None,
    ) -> None:
        self.token = token or os.getenv("COURSEFUZZ_GITHUB_TOKEN")
        configured_repositories = os.getenv("COURSEFUZZ_GITHUB_ALLOWED_REPOS", "")
        self.allowed_repositories = {
            repository.strip().lower()
            for repository in (
                allowed_repositories
                if allowed_repositories is not None
                else configured_repositories.split(",")
            )
            if repository.strip()
        }
        self.client = client or httpx.Client(
            base_url="https://api.github.com",
            timeout=10.0,
        )

    @property
    def available(self) -> bool:
        has_credentials = bool(self.token) or self.client.base_url.host != "api.github.com"
        return has_credentials and bool(self.allowed_repositories)

    def _require_allowed_repository(self, repository: str) -> None:
        if repository.lower() not in self.allowed_repositories:
            raise RuntimeError(
                f"GitHub repository {repository!r} is not in "
                "COURSEFUZZ_GITHUB_ALLOWED_REPOS"
            )

    def prepare(self, run_id: str, candidate: CandidatePatch) -> CandidatePatch:
        target = candidate.target
        if target.kind != "github_pull_request":
            return candidate
        if not self.available:
            raise RuntimeError(
                "GitHub destination requires COURSEFUZZ_GITHUB_TOKEN and a non-empty "
                "COURSEFUZZ_GITHUB_ALLOWED_REPOS allowlist"
            )
        if not target.repository or not target.base_branch:
            raise RuntimeError("GitHub destination is missing repository or base branch")
        self._require_allowed_repository(target.repository)

        encoded_branch = quote(target.base_branch, safe="")
        response = self._request(
            "GET",
            f"/repos/{target.repository}/git/ref/heads/{encoded_branch}",
        )
        base_commit_sha = str(response.json()["object"]["sha"])
        branch_suffix = candidate.id.removeprefix("patch-")[:8]
        head_branch = f"coursefuzz/{run_id}-{branch_suffix}"
        prepared_target = target.model_copy(
            update={
                "base_commit_sha": base_commit_sha,
                "head_branch": head_branch,
            }
        )
        return bind_candidate_payload(candidate.model_copy(update={"target": prepared_target}))

    def apply(self, candidate: CandidatePatch) -> AppliedDestination:
        target = candidate.target
        if (
            target.kind != "github_pull_request"
            or not target.repository
            or not target.base_branch
            or not target.base_commit_sha
            or not target.head_branch
        ):
            raise RuntimeError("GitHub candidate is not bound to an exact destination")
        self._require_allowed_repository(target.repository)

        encoded_head = quote(target.head_branch, safe="")
        branch_response = self._request(
            "POST",
            f"/repos/{target.repository}/git/refs",
            json={
                "ref": f"refs/heads/{target.head_branch}",
                "sha": target.base_commit_sha,
            },
            allowed={201, 422},
        )
        if branch_response.status_code == 422:
            self._request(
                "GET",
                f"/repos/{target.repository}/git/ref/heads/{encoded_head}",
            )

        existing = self._request(
            "GET",
            f"/repos/{target.repository}/contents/{target.path}",
            params={"ref": target.head_branch},
            allowed={200, 404},
        )
        expected_bytes = candidate.pytest_source.encode("utf-8")
        commit_sha: str | None = None
        if existing.status_code == 200:
            current_bytes = base64.b64decode(existing.json()["content"])
            if current_bytes != expected_bytes:
                write_payload = self._write_payload(candidate, existing.json()["sha"])
            else:
                write_payload = None
        else:
            write_payload = self._write_payload(candidate)

        if write_payload:
            written = self._request(
                "PUT",
                f"/repos/{target.repository}/contents/{target.path}",
                json=write_payload,
                allowed={200, 201},
            )
            commit_sha = str(written.json()["commit"]["sha"])

        owner = target.repository.split("/", 1)[0]
        pull = self._request(
            "POST",
            f"/repos/{target.repository}/pulls",
            json={
                "title": f"CourseFuzz: add verified regression for {candidate.test.label}",
                "body": self._pull_request_body(candidate),
                "head": target.head_branch,
                "base": target.base_branch,
                "draft": True,
            },
            allowed={201, 422},
        )
        if pull.status_code == 422:
            existing_pulls = self._request(
                "GET",
                f"/repos/{target.repository}/pulls",
                params={
                    "state": "open",
                    "head": f"{owner}:{target.head_branch}",
                    "base": target.base_branch,
                },
            ).json()
            if not existing_pulls:
                raise RuntimeError("GitHub rejected the pull request and no retryable PR exists")
            pull_data = existing_pulls[0]
        else:
            pull_data = pull.json()

        read_back = self._request(
            "GET",
            f"/repos/{target.repository}/contents/{target.path}",
            params={"ref": target.head_branch},
        ).json()
        read_back_bytes = base64.b64decode(read_back["content"])
        if read_back_bytes != expected_bytes:
            raise RuntimeError("GitHub destination read-back did not match the approved payload")
        artifact_sha256 = hashlib.sha256(read_back_bytes).hexdigest()

        return AppliedDestination(
            receipt=ActionReceipt(
                kind="github_pull_request",
                path=target.path,
                artifact_sha256=artifact_sha256,
                read_back_verified=True,
                external_url=str(pull_data["html_url"]),
                repository=target.repository,
                base_commit_sha=target.base_commit_sha,
                commit_sha=commit_sha,
                pull_request_number=int(pull_data["number"]),
            )
        )

    def check_runs(self, repository: str, commit_sha: str) -> CheckRunStatus:
        """Read the target repository's CI for one commit and aggregate its check-runs.

        Returns ``pending`` while any check is unfinished or none have appeared yet, ``success``
        when every completed check passes, and ``failure`` on the first non-passing conclusion.
        This does not merge or mutate anything — it only reads the destination's own CI conclusion.
        """

        self._require_allowed_repository(repository)
        encoded_sha = quote(commit_sha, safe="")
        response = self._request(
            "GET",
            f"/repos/{repository}/commits/{encoded_sha}/check-runs",
        )
        runs = response.json().get("check_runs") or []
        if not runs:
            return CheckRunStatus(state="pending")
        url = next((run.get("html_url") for run in runs if run.get("html_url")), None)
        if any(run.get("status") != "completed" for run in runs):
            return CheckRunStatus(state="pending", url=url)
        conclusions = [str(run.get("conclusion")) for run in runs]
        if all(conclusion in PASSING_CONCLUSIONS for conclusion in conclusions):
            return CheckRunStatus(state="success", conclusion="success", url=url)
        failing = next(c for c in conclusions if c not in PASSING_CONCLUSIONS)
        return CheckRunStatus(state="failure", conclusion=failing, url=url)

    def _write_payload(self, candidate: CandidatePatch, existing_sha: str | None = None) -> dict:
        target = candidate.target
        payload = {
            "message": f"Add CourseFuzz regression {candidate.id}",
            "content": base64.b64encode(candidate.pytest_source.encode("utf-8")).decode("ascii"),
            "branch": target.head_branch,
        }
        if existing_sha:
            payload["sha"] = existing_sha
        return payload

    @staticmethod
    def _pull_request_body(candidate: CandidatePatch) -> str:
        return (
            "## Verified assessment repair\n\n"
            f"- Counterexample: `{candidate.test.inputs}` -> `{candidate.test.expected}`\n"
            f"- Killed misconception programs: {len(candidate.target_mutants)}\n"
            f"- Approved payload SHA-256: `{candidate.payload_sha256}`\n"
            "- Correctness source: independent execution, not model self-evaluation\n\n"
            "This draft PR was created only after exact-payload approval. CourseFuzz read the "
            "destination bytes back before reporting verification."
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        allowed: set[int] | None = None,
        **kwargs: object,
    ) -> httpx.Response:
        allowed = allowed or {200}
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        last_response: httpx.Response | None = None
        for attempt in range(2):
            response = self.client.request(method, url, headers=headers, **kwargs)
            last_response = response
            if response.status_code in allowed:
                return response
            if response.status_code not in RETRYABLE_STATUSES or attempt == 1:
                break
            retry_after = min(float(response.headers.get("Retry-After", "0.2")), 1.0)
            time.sleep(max(retry_after, 0.0))
        assert last_response is not None
        detail = last_response.text[:500]
        raise RuntimeError(
            f"GitHub API {method} {url} failed with {last_response.status_code}: {detail}"
        )


class DestinationCoordinator:
    def __init__(
        self,
        artifact_dir: str | Path,
        github: GitHubDestinationAdapter | None = None,
    ) -> None:
        self.artifact_dir = Path(artifact_dir)
        self.github = github or GitHubDestinationAdapter()

    def prepare(self, run_id: str, candidate: CandidatePatch) -> CandidatePatch:
        if candidate.target.kind == "github_pull_request":
            return self.github.prepare(run_id, candidate)
        return candidate

    def apply(self, run_id: str, candidate: CandidatePatch) -> AppliedDestination:
        if candidate.target.kind == "github_pull_request":
            return self.github.apply(candidate)

        run_dir = (self.artifact_dir / run_id).resolve()
        artifact_path = (run_dir / candidate.target.path).resolve()
        if not artifact_path.is_relative_to(run_dir):
            raise RuntimeError("Local artifact target escaped the bounded run directory")
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(candidate.pytest_source.encode("utf-8"))
        read_back = artifact_path.read_bytes()
        if read_back.decode("utf-8") != candidate.pytest_source:
            raise RuntimeError("Artifact read-back did not match the approved payload")
        artifact_sha256 = hashlib.sha256(read_back).hexdigest()
        return AppliedDestination(
            receipt=ActionReceipt(
                kind="local_artifact",
                path=candidate.target.path,
                artifact_sha256=artifact_sha256,
                read_back_verified=True,
            ),
            local_path=artifact_path,
        )
