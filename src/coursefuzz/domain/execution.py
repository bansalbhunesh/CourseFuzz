"""Versioned execution contracts shared by every execution adapter.

`next.md` (docs/NEXT_STEPS.md) Milestone 1 requires moving the demo runner behind an
``ExecutionGateway`` so a hardened remote runner can replace the local one without touching
the engine, oracle, or services. This module defines the typed request/result contracts and
the gateway protocol. It deliberately contains no execution code: adapters live in
``coursefuzz.adapters``.

The contract is honest about its current scope. The receipt records the interpreter identity,
wall time, outcome, and a termination reason so that *every* execution — local or remote —
carries a runtime-pinned receipt, which is a Milestone 1 exit-gate requirement. Fields the
local runner cannot yet enforce out-of-process (CPU, memory, PID ceilings) are declared on
``ExecutionLimits`` so the remote adapter can populate them without another schema change.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from collections.abc import Sequence
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from coursefuzz.domain.models import JsonAtom, ProgramVariant, SuiteExecution, TestCase

EXECUTION_CONTRACT_VERSION = 1


class ExecutionOutcome(StrEnum):
    """The single outcome of one execution request against one program."""

    COMPLETED = "completed"          # ran to completion on every test; passed/failed are meaningful
    REJECTED = "rejected"            # source violated the restricted-language contract
    TIMED_OUT = "timed_out"          # wall deadline exceeded
    OUTPUT_LIMIT = "output_limit"    # captured output exceeded the byte ceiling
    RUNTIME_ERROR = "runtime_error"  # the runner produced no usable result


class ExecutionLimits(BaseModel):
    """Resource ceilings bound into a request and echoed by the receipt.

    ``wall_seconds``, ``output_bytes`` and ``source_bytes`` are enforced today by the local
    runner. ``cpu_seconds``, ``memory_bytes`` and ``max_pids`` are declared but only enforceable
    by a remote isolated runner; the local adapter records them as ``None`` rather than pretending
    to guarantee them.
    """

    model_config = ConfigDict(frozen=True)

    wall_seconds: float = Field(gt=0, le=60)
    output_bytes: int = Field(gt=0, le=8_000_000)
    source_bytes: int = Field(gt=0, le=1_000_000)
    cpu_seconds: float | None = Field(default=None, gt=0, le=60)
    memory_bytes: int | None = Field(default=None, gt=0)
    max_pids: int | None = Field(default=None, gt=0)


DEFAULT_LIMITS = ExecutionLimits(
    wall_seconds=1.5,
    output_bytes=1_000_000,
    source_bytes=16_384,
)


class ExecutionRequest(BaseModel):
    """One immutable, content-addressed unit of work for an execution gateway."""

    model_config = ConfigDict(frozen=True)

    contract_version: int = EXECUTION_CONTRACT_VERSION
    language: Literal["python"] = "python"
    program_id: str
    entrypoint: str
    source: str
    source_sha256: str
    tests: tuple[TestCase, ...]
    limits: ExecutionLimits = DEFAULT_LIMITS

    @classmethod
    def build(
        cls,
        *,
        program_id: str,
        source: str,
        entrypoint: str,
        tests: tuple[TestCase, ...] | list[TestCase],
        limits: ExecutionLimits = DEFAULT_LIMITS,
    ) -> ExecutionRequest:
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        return cls(
            program_id=program_id,
            source=source,
            source_sha256=digest,
            entrypoint=entrypoint,
            tests=tuple(tests),
            limits=limits,
        )

    @property
    def digest(self) -> str:
        """Deterministic identity of the work, independent of which adapter runs it."""

        payload = self.model_dump(mode="json", exclude={"source"})
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()


class CaseOutput(BaseModel):
    """Typed record of one test evaluation."""

    model_config = ConfigDict(frozen=True)

    inputs: tuple[int, ...]
    expected: JsonAtom | None = None
    actual: JsonAtom | None = None
    passed: bool
    error: str | None = None


class ExecutionReceipt(BaseModel):
    """Runtime-pinned evidence that a specific request ran on a specific interpreter."""

    model_config = ConfigDict(frozen=True)

    contract_version: int = EXECUTION_CONTRACT_VERSION
    request_digest: str
    runtime: str
    outcome: ExecutionOutcome
    termination_reason: str
    wall_ms: int
    output_bytes: int


class ExecutionResult(BaseModel):
    """The typed outcome of one execution request."""

    model_config = ConfigDict(frozen=True)

    outcome: ExecutionOutcome
    passed: int
    failed: int
    outputs: tuple[CaseOutput, ...] = ()
    error: str | None = None
    receipt: ExecutionReceipt

    @property
    def completed(self) -> bool:
        return self.outcome is ExecutionOutcome.COMPLETED


class ExecutionGateway(ABC):
    """Domain protocol shared by the local restricted runner and future isolated runners.

    A gateway is anything that can turn a versioned :class:`ExecutionRequest` into a versioned
    :class:`ExecutionResult` with a runtime-pinned receipt. The engine, services, and oracle must
    depend only on this protocol so the concrete sandbox (subprocess today, remote microVM later)
    stays swappable. Every gateway is validated by the same shared contract suite in
    ``tests/test_execution_gateway.py``.
    """

    contract_version: int = EXECUTION_CONTRACT_VERSION

    @abstractmethod
    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        raise NotImplementedError

    def execute_batch(self, requests: Sequence[ExecutionRequest]) -> list[ExecutionResult]:
        """Execute several requests, one result each, preserving order.

        The default runs them sequentially, so a runner whose per-execution overhead is negligible
        (the local process) needs no special handling. Container runners override this to run the
        whole batch in a single sandbox — one start-up instead of one per program — which is what
        makes a container-backed analysis practical.
        """

        return [self.execute(request) for request in requests]

    def run_suite(
        self,
        program: ProgramVariant,
        entrypoint: str,
        tests: tuple[TestCase, ...] | list[TestCase],
        timeout_seconds: float | None = None,
    ) -> SuiteExecution:
        """Engine-facing adapter over ``execute`` so any gateway is a drop-in execution backend.

        ``LocalRestrictedRunner`` overrides this with a byte-identical direct path; container
        runners inherit this adaptation unchanged. The output dicts match the legacy shape the
        engine reads (``outputs[i]["actual"]``).
        """

        if timeout_seconds:
            wall_seconds = min(timeout_seconds, 60.0)
        else:
            wall_seconds = DEFAULT_LIMITS.wall_seconds
        request = ExecutionRequest.build(
            program_id=program.id,
            source=program.source,
            entrypoint=entrypoint,
            tests=tuple(tests),
            limits=DEFAULT_LIMITS.model_copy(update={"wall_seconds": max(wall_seconds, 0.01)}),
        )
        result = self.execute(request)
        outputs: list[dict[str, Any]] = []
        for case in result.outputs:
            entry: dict[str, Any] = {
                "inputs": list(case.inputs),
                "expected": case.expected,
                "actual": case.actual,
                "passed": case.passed,
            }
            if case.error is not None:
                entry["error"] = case.error
            outputs.append(entry)
        return SuiteExecution(
            program_id=program.id,
            passed=result.passed,
            failed=result.failed,
            timed_out=result.outcome is ExecutionOutcome.TIMED_OUT,
            error=result.error,
            outputs=outputs,
        )
