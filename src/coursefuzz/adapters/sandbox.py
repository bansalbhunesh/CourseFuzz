from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from coursefuzz.domain.execution import (
    CaseOutput,
    ExecutionGateway,
    ExecutionOutcome,
    ExecutionReceipt,
    ExecutionRequest,
    ExecutionResult,
)
from coursefuzz.domain.models import ProgramVariant, SuiteExecution, TestCase

_RUNTIME = f"{platform.python_implementation().lower()}-{platform.python_version()}-{sys.platform}"


@dataclass(frozen=True)
class _RawRun:
    """Normalized result of one subprocess invocation.

    Both the legacy ``run_suite`` view (``SuiteExecution``) and the versioned ``execute`` view
    (``ExecutionResult``) are built from this single record, so the two paths can never drift.
    """

    outcome: ExecutionOutcome
    passed: int
    failed: int
    error: str | None
    termination_reason: str
    output_bytes: int
    wall_ms: int
    outputs: list[dict[str, Any]] = field(default_factory=list)


class LocalRestrictedRunner(ExecutionGateway):
    """Executes the restricted demo language in an isolated ``python -I`` process.

    This is a development-only containment boundary. It contains the restricted demo language at
    the source-AST level and enforces a wall deadline and an output ceiling out-of-process. It is
    explicitly *not* hostile-code isolation: `next.md` Milestone 1 requires a no-network remote
    ``RemoteIsolatedRunner`` behind the same :class:`ExecutionGateway` before any untrusted-code
    claim is made. The gateway seam exists so that swap needs no engine change.
    """

    def __init__(self, timeout_seconds: float = 1.5) -> None:
        self.timeout_seconds = timeout_seconds
        self.runner_path = Path(__file__).with_name("runner.py").resolve()

    # -- versioned gateway contract ---------------------------------------------------------

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        raw = self._invoke(
            source=request.source,
            entrypoint=request.entrypoint,
            tests=request.tests,
            timeout_seconds=request.limits.wall_seconds,
            output_limit=request.limits.output_bytes,
        )
        receipt = ExecutionReceipt(
            request_digest=request.digest,
            runtime=_RUNTIME,
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

    # -- legacy engine-facing view (behavior-preserving) ------------------------------------

    def run_suite(
        self,
        program: ProgramVariant,
        entrypoint: str,
        tests: tuple[TestCase, ...] | list[TestCase],
        timeout_seconds: float | None = None,
    ) -> SuiteExecution:
        raw = self._invoke(
            source=program.source,
            entrypoint=entrypoint,
            tests=tests,
            timeout_seconds=timeout_seconds,
            output_limit=1_000_000,
        )
        return SuiteExecution(
            program_id=program.id,
            passed=raw.passed,
            failed=raw.failed,
            timed_out=raw.outcome is ExecutionOutcome.TIMED_OUT,
            error=raw.error,
            outputs=raw.outputs,
        )

    def run_suite_batch(
        self,
        programs: Sequence[ProgramVariant],
        entrypoint: str,
        tests: tuple[TestCase, ...] | list[TestCase],
        timeout_seconds: float | None = None,
    ) -> list[SuiteExecution]:
        # The local process has negligible per-execution overhead, so batching is just its own
        # byte-identical run_suite looped: no behavior change relative to the sequential path.
        return [self.run_suite(program, entrypoint, tests, timeout_seconds) for program in programs]

    # -- shared subprocess invocation -------------------------------------------------------

    def _invoke(
        self,
        *,
        source: str,
        entrypoint: str,
        tests: tuple[TestCase, ...] | list[TestCase],
        timeout_seconds: float | None,
        output_limit: int,
    ) -> _RawRun:
        num_tests = len(tuple(tests))
        payload = {
            "source": source,
            "entrypoint": entrypoint,
            "tests": [test.model_dump(mode="json") for test in tests],
        }
        environment = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        timeout = min(timeout_seconds or self.timeout_seconds, self.timeout_seconds)
        started = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix="coursefuzz-") as workdir:
                completed = subprocess.run(  # noqa: S603
                    [sys.executable, "-I", str(self.runner_path)],
                    input=json.dumps(payload),
                    text=True,
                    capture_output=True,
                    cwd=workdir,
                    env=environment,
                    timeout=max(timeout, 0.01),
                    check=False,
                    creationflags=creation_flags,
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
        if len(completed.stdout) > output_limit:
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
            return _RawRun(
                outcome=(
                    ExecutionOutcome.REJECTED if rejected else ExecutionOutcome.RUNTIME_ERROR
                ),
                passed=0,
                failed=num_tests,
                error=str(result.get("error", "Runner failed")),
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


# Backwards-compatible alias: the engine, services, and existing tests import this name.
SubprocessPythonSandbox = LocalRestrictedRunner
