import base64
import json

import httpx
import pytest

from coursefuzz.domain.models import GitHubPullRequestDestination
from coursefuzz.security.access import LOCAL_TENANT
from coursefuzz.security.github_app import GitHubCredentialProvider
from coursefuzz.services.assignment_service import AssignmentService
from coursefuzz.services.github_importer import GitHubImportError, GitHubImporterService


class MockCredentialProvider(GitHubCredentialProvider):
    @property
    def available(self) -> bool:
        return True

    @property
    def repositories(self) -> frozenset[str]:
        return frozenset(["org/repo"])

    @property
    def mode(self) -> str:
        return "github-app"

    def allows(self, repository: str, tenant_id: str = LOCAL_TENANT) -> bool:
        return repository == "org/repo"

    def token_for(self, repository: str, tenant_id: str = LOCAL_TENANT) -> str | None:
        return "mock-token" if repository == "org/repo" else None


class MockTransport(httpx.MockTransport):
    def __init__(self):
        self.files = {}
        self.dirs = {}

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "/contents/" not in url:
                return httpx.Response(404, json={"message": "Not Found"})

            path_and_query = url.split("/contents/")[1]
            path = path_and_query.split("?")[0]

            if path in self.files:
                content = self.files[path]
                encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
                return httpx.Response(
                    200, json={"type": "file", "content": encoded, "encoding": "base64"}
                )

            if path in self.dirs:
                items = self.dirs[path]
                return httpx.Response(
                    200,
                    json=[
                        {"name": item, "path": f"{path}/{item}", "type": "file"} for item in items
                    ],
                )

            return httpx.Response(404, json={"message": "Not Found"})

        super().__init__(handler)


@pytest.fixture
def mock_client():
    transport = MockTransport()
    client = httpx.Client(transport=transport, base_url="https://api.github.com")
    client.transport_mock = transport
    return client


@pytest.fixture
def importer(mock_client, mock_assignment_service):
    provider = MockCredentialProvider()
    return GitHubImporterService(provider, mock_assignment_service, client=mock_client)


@pytest.fixture
def mock_assignment_service(tmp_path):
    from coursefuzz.adapters.sandbox import SubprocessPythonSandbox
    from coursefuzz.repositories.sqlite import RunRepository

    repo = RunRepository(tmp_path / "test.db")
    sandbox = SubprocessPythonSandbox()
    return AssignmentService(repo, sandbox)


def test_importer_success(importer, mock_client):
    assignment_json = {
        "title": "Imported Triangle",
        "entrypoint": "classify",
        "input_names": ["a", "b", "c"],
        "domain_min": 1,
        "domain_max": 10,
        "instructor_tests": [{"inputs": [1, 1, 1], "expected": "equilateral"}],
    }
    mock_client.transport_mock.files["assignment.json"] = json.dumps(assignment_json)
    mock_client.transport_mock.files["reference.py"] = (
        "def classify(a,b,c):\n  return 'equilateral'"
    )
    mock_client.transport_mock.dirs["accepted"] = ["control1.py"]
    mock_client.transport_mock.files["accepted/control1.py"] = (
        "def classify(a,b,c):\n  # control\n  return 'equilateral'"
    )
    mock_client.transport_mock.dirs["misconceptions"] = ["wrong1.py"]
    mock_client.transport_mock.files["misconceptions/wrong1.py"] = (
        "def classify(a,b,c):\n  return 'isosceles'"
    )

    assignment_id = importer.import_repository(
        repository="org/repo",
        tenant_id=LOCAL_TENANT,
        commit_sha="0000000000000000000000000000000000000000",
        branch="main",
    )

    assert assignment_id.startswith("asg_")

    # Retrieve and verify
    snapshot = importer._assignment_service.require(assignment_id, LOCAL_TENANT)
    assert snapshot.provenance == "github_import"
    assert snapshot.github_provenance.repository == "org/repo"
    assert snapshot.github_provenance.commit_sha == "0000000000000000000000000000000000000000"

    spec = snapshot.spec
    assert spec.title == "Imported Triangle"
    assert spec.entrypoint == "classify"
    assert len(spec.accepted_solutions) == 2
    assert len(spec.mutants) == 1
    assert len(spec.instructor_tests) == 1

    assert isinstance(spec.destination, GitHubPullRequestDestination)
    assert spec.destination.repository == "org/repo"
    assert spec.destination.base_branch == "main"


def test_importer_missing_assignment_json(importer, mock_client):
    with pytest.raises(GitHubImportError, match="assignment.json is missing"):
        importer.import_repository("org/repo", LOCAL_TENANT, "sha")


def test_importer_missing_reference(importer, mock_client):
    mock_client.transport_mock.files["assignment.json"] = "{}"
    with pytest.raises(GitHubImportError, match="Reference solution not found"):
        importer.import_repository("org/repo", LOCAL_TENANT, "sha")


def test_importer_unauthorized_repository(importer):
    with pytest.raises(GitHubImportError, match="is not authorized"):
        importer.import_repository("other/repo", LOCAL_TENANT, "sha")
