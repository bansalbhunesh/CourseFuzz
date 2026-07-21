"""Tests for the container ExecutionGateway adapter and its gVisor target.

The isolation posture lives entirely in the ``docker run`` argument vector, so most of it is proven
here without a daemon by injecting a fake executor and asserting the exact flags. One end-to-end
test runs a real container and is skipped unless the daemon and image are present. The gVisor
(``runsc``) runtime is asserted at the argv level; its live run belongs on a runsc-equipped host.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field

import pytest

from coursefuzz.adapters.batch_runner import run_batch
from coursefuzz.adapters.isolated_runner import (
    CommandResult,
    DockerIsolatedRunner,
    GVisorDockerRunner,
)
from coursefuzz.adapters.sandbox import LocalRestrictedRunner
from coursefuzz.domain.execution import ExecutionLimits, ExecutionOutcome, ExecutionRequest
from coursefuzz.domain.models import ProgramVariant
from coursefuzz.domain.models import TestCase as CFTestCase

INCREMENT = "def solve(value):\n    return value + 1\n"


def _program(source: str = INCREMENT) -> ProgramVariant:
    return ProgramVariant(id="candidate", title="candidate", misconception="none", source=source)


@dataclass
class _Call:
    argv: list[str]
    input: str
    timeout: float


@dataclass
class _FakeExecutor:
    result: CommandResult | None = None
    timeout: bool = False
    calls: list[_Call] = field(default_factory=list)

    def run(self, argv: list[str], *, input: str, timeout: float) -> CommandResult:
        self.calls.append(_Call(argv, input, timeout))
        if self.timeout:
            raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)
        assert self.result is not None
        return self.result


def _default_limits() -> ExecutionLimits:
    return ExecutionLimits(wall_seconds=1.5, output_bytes=1_000_000, source_bytes=16_384)


def _request(source: str = INCREMENT, *, limits: ExecutionLimits | None = None) -> ExecutionRequest:
    return ExecutionRequest.build(
        program_id="candidate",
        source=source,
        entrypoint="solve",
        tests=(CFTestCase(inputs=(1,), expected=2, label="probe", source="deterministic"),),
        limits=limits or _default_limits(),
    )


def _completed_stdout(passed: int = 1, failed: int = 0) -> str:
    return json.dumps(
        {
            "ok": True,
            "passed": passed,
            "failed": failed,
            "outputs": [{"inputs": [1], "expected": 2, "actual": 2, "passed": True}],
        }
    )


def _index_pair(argv: list[str], flag: str) -> str | None:
    """Return the value immediately following ``flag`` in argv, or None if absent."""

    for i, token in enumerate(argv[:-1]):
        if token == flag:
            return argv[i + 1]
    return None


def test_isolation_argv_disables_network_and_privileges() -> None:
    executor = _FakeExecutor(result=CommandResult(0, _completed_stdout(), ""))
    runner = DockerIsolatedRunner(executor=executor)

    runner.execute(_request())
    argv = executor.calls[0].argv

    assert _index_pair(argv, "--network") == "none"
    assert "--read-only" in argv
    assert _index_pair(argv, "--cap-drop") == "ALL"
    assert _index_pair(argv, "--security-opt") == "no-new-privileges"
    assert "--rm" in argv
    # A code sandbox must never bind-mount a host path in.
    assert "-v" not in argv and "--volume" not in argv and "--mount" not in argv
    # The same restricted runner executes inside the container.
    assert argv[-3:] == ["-I", "-m", "coursefuzz.adapters.runner"]


def test_gvisor_runner_selects_the_runsc_runtime() -> None:
    executor = _FakeExecutor(result=CommandResult(0, _completed_stdout(), ""))
    runner = GVisorDockerRunner(executor=executor)

    result = runner.execute(_request())
    argv = executor.calls[0].argv

    assert _index_pair(argv, "--runtime") == "runsc"
    assert result.receipt.runtime == "docker/runsc:coursefuzz:local"


def test_default_runner_omits_the_runtime_flag() -> None:
    executor = _FakeExecutor(result=CommandResult(0, _completed_stdout(), ""))
    runner = DockerIsolatedRunner(executor=executor)

    runner.execute(_request())

    assert "--runtime" not in executor.calls[0].argv


def test_request_limits_flow_into_container_ceilings() -> None:
    executor = _FakeExecutor(result=CommandResult(0, _completed_stdout(), ""))
    runner = DockerIsolatedRunner(executor=executor)
    limits = ExecutionLimits(
        wall_seconds=2.0,
        output_bytes=1_000_000,
        source_bytes=16_384,
        memory_bytes=128 * 1024 * 1024,
        max_pids=32,
    )

    runner.execute(_request(limits=limits))
    argv = executor.calls[0].argv

    assert _index_pair(argv, "--memory") == f"{128 * 1024 * 1024}b"
    assert _index_pair(argv, "--pids-limit") == "32"
    assert _index_pair(argv, "--ulimit") == "nproc=32:32"
    # The subprocess deadline is the guest wall budget plus the container startup allowance.
    assert executor.calls[0].timeout == pytest.approx(2.0 + runner.startup_grace_seconds)


def test_completed_output_is_mapped() -> None:
    executor = _FakeExecutor(result=CommandResult(0, _completed_stdout(passed=1, failed=0), ""))
    runner = DockerIsolatedRunner(executor=executor)

    result = runner.execute(_request())

    assert result.outcome is ExecutionOutcome.COMPLETED
    assert result.passed == 1
    assert result.failed == 0
    assert result.outputs[0].actual == 2
    assert result.receipt.runtime == "docker/default:coursefuzz:local"
    assert result.receipt.termination_reason == "completed"


def test_rejected_output_is_mapped() -> None:
    stdout = json.dumps(
        {"ok": False, "error": "ValueError: Unsupported syntax: Import", "kind": "rejected"}
    )
    executor = _FakeExecutor(result=CommandResult(1, stdout, ""))
    runner = DockerIsolatedRunner(executor=executor)

    result = runner.execute(_request("import os\ndef solve(value):\n    return value\n"))

    assert result.outcome is ExecutionOutcome.REJECTED
    assert result.receipt.termination_reason == "restricted-language-violation"


def test_timeout_is_mapped() -> None:
    executor = _FakeExecutor(timeout=True)
    runner = DockerIsolatedRunner(executor=executor)

    result = runner.execute(_request())

    assert result.outcome is ExecutionOutcome.TIMED_OUT
    assert result.error == "Execution exceeded the total deadline"
    assert result.receipt.termination_reason == "wall-deadline-exceeded"


def test_infrastructure_failure_is_a_runtime_error() -> None:
    # Daemon down or image missing: no runner JSON, non-zero exit -> honest runtime error.
    executor = _FakeExecutor(result=CommandResult(125, "", "Cannot connect to the Docker daemon"))
    runner = DockerIsolatedRunner(executor=executor)

    result = runner.execute(_request())

    assert result.outcome is ExecutionOutcome.RUNTIME_ERROR
    assert "Docker daemon" in (result.error or "")


def test_run_suite_adapts_execute_for_the_engine_interface() -> None:
    executor = _FakeExecutor(result=CommandResult(0, _completed_stdout(passed=1, failed=0), ""))
    runner = DockerIsolatedRunner(executor=executor)
    tests = (CFTestCase(inputs=(1,), expected=2, label="probe", source="deterministic"),)

    suite = runner.run_suite(_program(), "solve", tests)

    # The AssessmentEngine drives execution through run_suite and reads outputs[i]["actual"].
    assert suite.all_passed
    assert suite.passed == 1
    assert suite.failed == 0
    assert suite.outputs[0]["actual"] == 2


REJECT_IMPORT = "import os\ndef solve(value):\n    return value\n"


def _batch_stdout(results: list[dict]) -> str:
    return json.dumps({"ok": True, "results": results})


def test_batch_runner_runs_each_program_in_order() -> None:
    payload = {
        "batch": [
            {"source": INCREMENT, "entrypoint": "solve", "tests": [{"inputs": [1], "expected": 2}]},
            {"source": REJECT_IMPORT, "entrypoint": "solve", "tests": [{"inputs": [1]}]},
        ]
    }

    result = run_batch(payload)

    assert result["ok"]
    assert result["results"][0]["ok"] and result["results"][0]["passed"] == 1
    assert not result["results"][1]["ok"]
    assert result["results"][1]["kind"] == "rejected"


def test_execute_batch_uses_one_container_for_many_programs() -> None:
    executor = _FakeExecutor(
        result=CommandResult(
            0,
            _batch_stdout(
                [
                    {
                        "ok": True,
                        "passed": 1,
                        "failed": 0,
                        "outputs": [{"inputs": [1], "expected": 2, "actual": 2, "passed": True}],
                    },
                    {"ok": False, "error": "ValueError: bad import", "kind": "rejected"},
                ]
            ),
            "",
        )
    )
    runner = DockerIsolatedRunner(executor=executor)

    results = runner.execute_batch([_request(INCREMENT), _request(REJECT_IMPORT)])

    # One container start-up for the whole batch, running the batch entrypoint.
    assert len(executor.calls) == 1
    assert executor.calls[0].argv[-3:] == ["-I", "-m", "coursefuzz.adapters.batch_runner"]
    assert len(results) == 2
    assert results[0].outcome is ExecutionOutcome.COMPLETED
    assert results[0].passed == 1
    assert results[1].outcome is ExecutionOutcome.REJECTED


def test_execute_batch_empty_returns_empty() -> None:
    executor = _FakeExecutor(result=CommandResult(0, "", ""))
    runner = DockerIsolatedRunner(executor=executor)

    assert runner.execute_batch([]) == []
    assert executor.calls == []  # no container is started for an empty batch


def test_local_runner_execute_batch_defaults_to_sequential() -> None:
    runner = LocalRestrictedRunner()

    results = runner.execute_batch([_request(INCREMENT), _request(REJECT_IMPORT)])

    assert results[0].outcome is ExecutionOutcome.COMPLETED
    assert results[1].outcome is ExecutionOutcome.REJECTED


def _docker_image_ready() -> bool:
    try:
        info = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, text=True, timeout=15,
        )
        if info.returncode != 0:
            return False
        image = subprocess.run(
            ["docker", "image", "inspect", "coursefuzz:local"],
            capture_output=True, text=True, timeout=15,
        )
        return image.returncode == 0
    except Exception:
        return False


def _gvisor_ready() -> bool:
    if not _docker_image_ready():
        return False
    try:
        info = subprocess.run(
            ["docker", "info", "--format", "{{range $k,$v := .Runtimes}}{{$k}} {{end}}"],
            capture_output=True, text=True, timeout=15,
        )
        return "runsc" in info.stdout.split()
    except Exception:
        return False


@pytest.mark.skipif(
    not _docker_image_ready(),
    reason="requires a running Docker daemon and a built coursefuzz:local image",
)
def test_container_executes_a_completed_batch_end_to_end() -> None:
    # Default runtime (runc) proves the real container path; runsc is the same argv on a runsc host.
    runner = DockerIsolatedRunner()

    result = runner.execute(_request())

    assert result.outcome is ExecutionOutcome.COMPLETED
    assert result.passed == 1
    assert result.failed == 0
    assert result.receipt.runtime == "docker/default:coursefuzz:local"


@pytest.mark.skipif(
    not _docker_image_ready(),
    reason="requires a running Docker daemon and a built coursefuzz:local image",
)
def test_no_network_flag_actually_denies_network() -> None:
    """Prove the isolation is real, not just declared: a socket attempt inside a --network none
    container fails. This checks the flag the argv test asserts actually isolates on the host.
    """

    probe = "import socket; socket.create_connection(('1.1.1.1', 53), timeout=3)"
    completed = subprocess.run(  # noqa: S603
        [
            "docker", "run", "--rm", "--network", "none",
            "--entrypoint", "python", "coursefuzz:local", "-c", probe,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode != 0  # the connection could not be established
    assert "unreachable" in completed.stderr.lower() or "network" in completed.stderr.lower()


@pytest.mark.skipif(
    not _docker_image_ready(),
    reason="requires a running Docker daemon and a built coursefuzz:local image",
)
def test_run_suite_executes_in_a_real_container() -> None:
    """The engine-facing run_suite runs correctly through a real container, so the isolated
    runner is a drop-in execution backend for AssessmentEngine.
    """

    runner = DockerIsolatedRunner()
    tests = (CFTestCase(inputs=(1,), expected=2, label="probe", source="deterministic"),)

    suite = runner.run_suite(_program(), "solve", tests)

    assert suite.all_passed
    assert suite.passed == 1
    assert suite.failed == 0


@pytest.mark.skipif(
    not _gvisor_ready(),
    reason="requires the runsc (gVisor) runtime registered with Docker and the image",
)
def test_gvisor_runtime_executes_in_a_real_container() -> None:
    """The production target runs end to end under gVisor's runsc runtime. Skips unless runsc is
    installed (it is on the CI isolated-runner job); the argv contract is asserted unconditionally.
    """

    runner = GVisorDockerRunner()

    result = runner.execute(_request())

    assert result.outcome is ExecutionOutcome.COMPLETED
    assert result.passed == 1
    assert result.receipt.runtime == "docker/runsc:coursefuzz:local"


@pytest.mark.skipif(
    not _docker_image_ready(),
    reason="requires a running Docker daemon and a built coursefuzz:local image",
)
def test_execute_batch_runs_many_programs_in_one_real_container() -> None:
    """Two programs run in a single container start-up, each mapped to its own result."""

    runner = DockerIsolatedRunner()

    results = runner.execute_batch([_request(INCREMENT), _request(REJECT_IMPORT)])

    assert len(results) == 2
    assert results[0].outcome is ExecutionOutcome.COMPLETED
    assert results[0].passed == 1
    assert results[1].outcome is ExecutionOutcome.REJECTED
