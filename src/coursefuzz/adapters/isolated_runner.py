"""A no-network container adapter for the ExecutionGateway (roadmap Milestone 1).

``LocalRestrictedRunner`` contains the restricted demo language at the source-AST level. This
adapter runs the *same* runner inside a throwaway container whose isolation does not depend on that
allowlist: the network is disabled, all Linux capabilities are dropped, the root filesystem is
read-only with a small tmpfs, privilege escalation is blocked, and memory/PID ceilings are enforced
out of the guest. The entire isolation posture is expressed in the ``docker run`` argument vector,
so ``tests/test_docker_isolated_runner.py`` asserts it directly through an injected executor without
requiring a live daemon; a daemon-gated test runs the real container end to end.

This adapter does not, on its own, make the AST allowlist safe to remove — seccomp/user-namespace
policy and image provenance are still required before running genuinely arbitrary code — but it is a
real defense-in-depth boundary the local runner cannot provide.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from coursefuzz.domain.execution import (
    CaseOutput,
    ExecutionGateway,
    ExecutionOutcome,
    ExecutionReceipt,
    ExecutionRequest,
    ExecutionResult,
)

DEFAULT_IMAGE = "coursefuzz:local"
DEFAULT_MEMORY_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_PIDS = 128
DEFAULT_CONTAINER_USER = "10001:10001"  # the non-root coursefuzz uid baked into the image


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class CommandExecutor(Protocol):
    """Seam so the isolation argv can be asserted without executing Docker."""

    def run(self, argv: list[str], *, input: str, timeout: float) -> CommandResult: ...


class SubprocessCommandExecutor:
    def run(self, argv: list[str], *, input: str, timeout: float) -> CommandResult:
        completed = subprocess.run(  # noqa: S603
            argv,
            input=input,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)


@dataclass(frozen=True)
class _RawRun:
    outcome: ExecutionOutcome
    passed: int
    failed: int
    error: str | None
    termination_reason: str
    output_bytes: int
    wall_ms: int
    outputs: list[dict[str, Any]] = field(default_factory=list)


class DockerIsolatedRunner(ExecutionGateway):
    """Executes one restricted-language batch inside a throwaway, no-network container."""

    def __init__(
        self,
        image: str = DEFAULT_IMAGE,
        executor: CommandExecutor | None = None,
        *,
        runtime: str | None = None,
        cpus: str = "1.0",
        tmpfs_mb: int = 16,
        container_user: str = DEFAULT_CONTAINER_USER,
        startup_grace_seconds: float = 15.0,
    ) -> None:
        self.image = image
        self.executor = executor or SubprocessCommandExecutor()
        # Container creation/start is overhead the student-code wall budget should not absorb, so
        # the subprocess deadline is the request's wall budget plus a fixed startup allowance.
        self.startup_grace_seconds = startup_grace_seconds
        # ``runtime`` selects the OCI runtime, e.g. "runsc" for gVisor. None uses the engine
        # default (runc). The runtime is the swap point between defense-in-depth (runc + the
        # flags below) and a syscall-filtering sandbox (gVisor) suitable for arbitrary code.
        self.runtime = runtime
        self.cpus = cpus
        self.tmpfs_mb = tmpfs_mb
        self.container_user = container_user

    @property
    def runtime_identity(self) -> str:
        return f"docker/{self.runtime or 'default'}:{self.image}"

    def _container_argv(self, memory_bytes: int, max_pids: int, module: str) -> list[str]:
        runtime_flag = ["--runtime", self.runtime] if self.runtime else []
        return [
            "docker",
            "run",
            "--rm",
            "--interactive",
            *runtime_flag,
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            f"/tmp:size={self.tmpfs_mb}m,mode=1777",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(max_pids),
            "--memory",
            f"{memory_bytes}b",
            "--memory-swap",
            f"{memory_bytes}b",
            "--cpus",
            self.cpus,
            "--user",
            self.container_user,
            "--workdir",
            "/tmp",
            "--entrypoint",
            "python",
            self.image,
            "-I",
            "-m",
            module,
        ]

    def isolation_argv(self, request: ExecutionRequest) -> list[str]:
        """The exact ``docker run`` invocation. Isolation lives here and is tested here."""

        memory_bytes = request.limits.memory_bytes or DEFAULT_MEMORY_BYTES
        max_pids = request.limits.max_pids or DEFAULT_MAX_PIDS
        return self._container_argv(memory_bytes, max_pids, "coursefuzz.adapters.runner")

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        raw = self._invoke(request)
        receipt = ExecutionReceipt(
            request_digest=request.digest,
            runtime=self.runtime_identity,
            outcome=raw.outcome,
            termination_reason=raw.termination_reason,
            wall_ms=raw.wall_ms,
            output_bytes=raw.output_bytes,
        )
        outputs = tuple(
            CaseOutput(
                inputs=tuple(entry.get("inputs", ())),
                expected=entry.get("expected"),
                actual=entry.get("actual"),
                passed=bool(entry.get("passed", False)),
                error=entry.get("error"),
            )
            for entry in raw.outputs
        )
        return ExecutionResult(
            outcome=raw.outcome,
            passed=raw.passed,
            failed=raw.failed,
            outputs=outputs,
            error=raw.error,
            receipt=receipt,
        )

    def execute_batch(self, requests: Sequence[ExecutionRequest]) -> list[ExecutionResult]:
        """Run every request in one container: a single start-up for the whole batch.

        Per-program validation and execution are identical to ``execute`` (both call the same
        restricted runner); only the container start-up is shared. Per-result ``output_bytes`` is
        not separable from one shared stdout, so it is reported as 0 for batched results.
        """

        batch = tuple(requests)
        if not batch:
            return []
        payload = {
            "batch": [
                {
                    "source": request.source,
                    "entrypoint": request.entrypoint,
                    "tests": [test.model_dump(mode="json") for test in request.tests],
                }
                for request in batch
            ]
        }
        memory_bytes = max((r.limits.memory_bytes or DEFAULT_MEMORY_BYTES) for r in batch)
        max_pids = max((r.limits.max_pids or DEFAULT_MAX_PIDS) for r in batch)
        argv = self._container_argv(memory_bytes, max_pids, "coursefuzz.adapters.batch_runner")
        wall = sum(r.limits.wall_seconds for r in batch) + self.startup_grace_seconds
        started = time.monotonic()
        try:
            completed = self.executor.run(argv, input=json.dumps(payload), timeout=max(wall, 0.01))
        except subprocess.TimeoutExpired:
            wall_ms = self._elapsed_ms(started)
            return [
                self._assemble(
                    r, ExecutionOutcome.TIMED_OUT, 0, len(r.tests),
                    "Execution exceeded the total deadline", "wall-deadline-exceeded", wall_ms, (),
                )
                for r in batch
            ]

        wall_ms = self._elapsed_ms(started)
        try:
            parsed = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            parsed = {"ok": False}
        results = parsed.get("results")
        if not parsed.get("ok") or not isinstance(results, list) or len(results) != len(batch):
            error = str(
                parsed.get("error")
                or (completed.stderr[:500] if completed.stderr else "Batch runner failed")
            )
            return [
                self._assemble(
                    r, ExecutionOutcome.RUNTIME_ERROR, 0, len(r.tests),
                    error, "runner-error", wall_ms, (),
                )
                for r in batch
            ]
        return [
            self._result_from_runner_dict(r, raw, wall_ms)
            for r, raw in zip(batch, results, strict=True)
        ]

    def _result_from_runner_dict(
        self, request: ExecutionRequest, raw: dict[str, Any], wall_ms: int
    ) -> ExecutionResult:
        num_tests = len(request.tests)
        if not raw.get("ok"):
            rejected = raw.get("kind") == "rejected"
            return self._assemble(
                request,
                ExecutionOutcome.REJECTED if rejected else ExecutionOutcome.RUNTIME_ERROR,
                0,
                num_tests,
                str(raw.get("error", "Runner failed")),
                "restricted-language-violation" if rejected else "runner-error",
                wall_ms,
                (),
            )
        outputs = tuple(
            CaseOutput(
                inputs=tuple(entry.get("inputs", ())),
                expected=entry.get("expected"),
                actual=entry.get("actual"),
                passed=bool(entry.get("passed", False)),
                error=entry.get("error"),
            )
            for entry in raw["outputs"]
        )
        return self._assemble(
            request,
            ExecutionOutcome.COMPLETED,
            int(raw["passed"]),
            int(raw["failed"]),
            None,
            "completed",
            wall_ms,
            outputs,
        )

    def _assemble(
        self,
        request: ExecutionRequest,
        outcome: ExecutionOutcome,
        passed: int,
        failed: int,
        error: str | None,
        termination_reason: str,
        wall_ms: int,
        outputs: tuple[CaseOutput, ...],
    ) -> ExecutionResult:
        return ExecutionResult(
            outcome=outcome,
            passed=passed,
            failed=failed,
            outputs=outputs,
            error=error,
            receipt=ExecutionReceipt(
                request_digest=request.digest,
                runtime=self.runtime_identity,
                outcome=outcome,
                termination_reason=termination_reason,
                wall_ms=wall_ms,
                output_bytes=0,
            ),
        )

    def _invoke(self, request: ExecutionRequest) -> _RawRun:
        num_tests = len(request.tests)
        payload = {
            "source": request.source,
            "entrypoint": request.entrypoint,
            "tests": [test.model_dump(mode="json") for test in request.tests],
        }
        argv = self.isolation_argv(request)
        started = time.monotonic()
        try:
            completed = self.executor.run(
                argv,
                input=json.dumps(payload),
                timeout=max(request.limits.wall_seconds + self.startup_grace_seconds, 0.01),
            )
        except subprocess.TimeoutExpired:
            return _RawRun(
                outcome=ExecutionOutcome.TIMED_OUT,
                passed=0,
                failed=num_tests,
                error="Execution exceeded the total deadline",
                termination_reason="wall-deadline-exceeded",
                output_bytes=0,
                wall_ms=self._elapsed_ms(started),
            )

        wall_ms = self._elapsed_ms(started)
        output_bytes = len(completed.stdout.encode("utf-8"))
        if len(completed.stdout) > request.limits.output_bytes:
            return _RawRun(
                outcome=ExecutionOutcome.OUTPUT_LIMIT,
                passed=0,
                failed=num_tests,
                error="Execution output exceeded the 1 MB limit",
                termination_reason="output-limit-exceeded",
                output_bytes=output_bytes,
                wall_ms=wall_ms,
            )
        try:
            result = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            result = {"ok": False, "error": "Runner returned malformed JSON"}
        if not result.get("ok"):
            rejected = result.get("kind") == "rejected"
            # A non-zero container exit with no runner JSON is an infrastructure failure
            # (daemon down, image missing), surfaced honestly as a runtime error.
            error = str(
                result.get("error")
                or (completed.stderr[:500] if completed.stderr else "Runner failed")
            )
            return _RawRun(
                outcome=(
                    ExecutionOutcome.REJECTED if rejected else ExecutionOutcome.RUNTIME_ERROR
                ),
                passed=0,
                failed=num_tests,
                error=error,
                termination_reason=(
                    "restricted-language-violation" if rejected else "runner-error"
                ),
                output_bytes=output_bytes,
                wall_ms=wall_ms,
            )
        return _RawRun(
            outcome=ExecutionOutcome.COMPLETED,
            passed=int(result["passed"]),
            failed=int(result["failed"]),
            error=None,
            termination_reason="completed",
            output_bytes=output_bytes,
            wall_ms=wall_ms,
            outputs=result["outputs"],
        )

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return max(0, round((time.monotonic() - started) * 1000))


class GVisorDockerRunner(DockerIsolatedRunner):
    """The named production target: Docker Engine using gVisor's ``runsc`` runtime.

    gVisor interposes a user-space kernel between the guest and the host syscall surface, which is
    the property that makes it appropriate for genuinely untrusted code — beyond what runc plus the
    capability/network/filesystem flags provide. It runs on a Linux worker with ``runsc`` installed
    and registered as a Docker runtime; the same argv and result contract are exercised locally with
    the default runtime, so the runsc live run is a CI/host concern, not a code difference.
    """

    def __init__(
        self,
        image: str = DEFAULT_IMAGE,
        executor: CommandExecutor | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(image, executor, runtime="runsc", **kwargs)
