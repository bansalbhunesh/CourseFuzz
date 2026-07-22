from __future__ import annotations

import base64
import json

import httpx

from coursefuzz.domain.models import (
    AssignmentCreate,
    GitHubImportProvenance,
    GitHubPullRequestDestination,
    InstructorTestInput,
    ProgramSourceInput,
)
from coursefuzz.security.github_app import GitHubCredentialProvider
from coursefuzz.services.assignment_service import AssignmentService


class GitHubImportError(Exception):
    pass


class GitHubImporterService:
    def __init__(
        self,
        provider: GitHubCredentialProvider,
        assignment_service: AssignmentService,
        client: httpx.Client | None = None,
    ) -> None:
        self._provider = provider
        self._assignment_service = assignment_service
        self._client = client or httpx.Client(base_url="https://api.github.com", timeout=10.0)

    def _get_headers(self, repository: str, tenant_id: str) -> dict[str, str]:
        token = self._provider.token_for(repository, tenant_id)
        if not token:
            raise GitHubImportError(f"No installation token available for repository {repository}")
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _fetch_file(
        self, repository: str, commit_sha: str, path: str, headers: dict[str, str]
    ) -> str | None:
        response = self._client.get(
            f"/repos/{repository}/contents/{path}?ref={commit_sha}",
            headers=headers,
        )
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise GitHubImportError(
                f"Failed to fetch {path} from {repository} at {commit_sha}: {response.status_code} {response.text}"
            )
        data = response.json()
        if data.get("type") != "file":
            raise GitHubImportError(f"Path {path} is not a file")

        content = data.get("content", "")
        encoding = data.get("encoding", "")
        if encoding == "base64":
            return base64.b64decode(content).decode("utf-8")
        return content

    def _list_directory(
        self, repository: str, commit_sha: str, path: str, headers: dict[str, str]
    ) -> list[dict[str, str]]:
        response = self._client.get(
            f"/repos/{repository}/contents/{path}?ref={commit_sha}",
            headers=headers,
        )
        if response.status_code == 404:
            return []
        if response.status_code != 200:
            raise GitHubImportError(
                f"Failed to list directory {path} in {repository}: {response.status_code} {response.text}"
            )
        data = response.json()
        if not isinstance(data, list):
            return []
        return [item for item in data if item.get("type") == "file"]

    def import_repository(
        self,
        repository: str,
        tenant_id: str,
        commit_sha: str,
        branch: str = "main",
        webhook_delivery_id: str | None = None,
        installation_id: int | None = None,
    ) -> str:
        """Imports an assignment from a GitHub repository and returns the assignment ID."""
        if not self._provider.allows(repository, tenant_id):
            raise GitHubImportError(
                f"Tenant {tenant_id} is not authorized for repository {repository}"
            )

        headers = self._get_headers(repository, tenant_id)

        # 1. Read assignment.json
        config_text = self._fetch_file(repository, commit_sha, "assignment.json", headers)
        if not config_text:
            raise GitHubImportError("assignment.json is missing in the repository root")

        try:
            config = json.loads(config_text)
        except json.JSONDecodeError as exc:
            raise GitHubImportError(f"Invalid assignment.json: {exc}") from exc

        # 2. Read reference solution
        ref_path = config.get("reference_path", "reference.py")
        ref_source = self._fetch_file(repository, commit_sha, ref_path, headers)
        if not ref_source:
            raise GitHubImportError(f"Reference solution not found at {ref_path}")

        reference = ProgramSourceInput(
            title="Reference Solution",
            source=ref_source,
            misconception="none",
        )

        # 3. Read accepted controls
        accepted_solutions = []
        for item in self._list_directory(repository, commit_sha, "accepted", headers):
            if not item["name"].endswith(".py"):
                continue
            source = self._fetch_file(repository, commit_sha, item["path"], headers)
            if source:
                accepted_solutions.append(
                    ProgramSourceInput(
                        title=item["name"],
                        source=source,
                        misconception="none",
                    )
                )

        if not accepted_solutions:
            raise GitHubImportError(
                "At least one accepted control program is required in the 'accepted' directory"
            )

        # 4. Read misconception submissions
        misconceptions = []
        for item in self._list_directory(repository, commit_sha, "misconceptions", headers):
            if not item["name"].endswith(".py"):
                continue
            source = self._fetch_file(repository, commit_sha, item["path"], headers)
            if source:
                misconceptions.append(
                    ProgramSourceInput(
                        title=item["name"],
                        source=source,
                        misconception="Unknown issue",
                    )
                )

        if not misconceptions:
            raise GitHubImportError(
                "At least one misconception program is required in the 'misconceptions' directory"
            )

        # 5. Build instructor tests
        tests_data = config.get("instructor_tests", [])
        if not tests_data:
            raise GitHubImportError("assignment.json must specify 'instructor_tests'")

        instructor_tests = []
        for test in tests_data:
            instructor_tests.append(
                InstructorTestInput(
                    inputs=tuple(test["inputs"]),
                    expected=test["expected"],
                    label=test.get("label", "Instructor test"),
                )
            )

        # 6. Parse domain and input names
        input_names = tuple(config.get("input_names", []))
        domain_min = config.get("domain_min", -1000)
        domain_max = config.get("domain_max", 1000)
        entrypoint = config.get("entrypoint", "solution")

        destination = GitHubPullRequestDestination(
            repository=repository,
            base_branch=branch,
            test_directory=config.get("test_directory", "tests/coursefuzz"),
        )

        payload = AssignmentCreate(
            title=config.get("title", f"Imported from {repository}"),
            summary=config.get("summary", f"Imported assignment from {repository} at {commit_sha}"),
            entrypoint=entrypoint,
            input_names=input_names,
            domain_min=domain_min,
            domain_max=domain_max,
            reference=reference,
            accepted_solutions=tuple(accepted_solutions[:7]),
            misconception_programs=tuple(misconceptions[:64]),
            instructor_tests=tuple(instructor_tests),
            destination=destination,
        )

        provenance = GitHubImportProvenance(
            installation_id=installation_id or 0,
            repository=repository,
            commit_sha=commit_sha,
            branch=branch,
            webhook_delivery_id=webhook_delivery_id,
        )

        snapshot, _ = self._assignment_service.create(
            payload,
            tenant_id,
            provenance="github_import",
            github_provenance=provenance,
        )
        return snapshot.id
