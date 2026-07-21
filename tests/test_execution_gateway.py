"""Shared contract suite for every ExecutionGateway.

`next.md` Milestone 1 requires that the local restricted runner and any future remote isolated
runner satisfy one adapter contract. These tests are parametrized over gateway factories so the
remote runner, when it lands, reuses this exact suite by appending its factory to
``GATEWAY_FACTORIES`` — no test rewrite.
"""

from __future__ import annotations

import pytest

from coursefuzz.adapters.sandbox import LocalRestrictedRunner
from coursefuzz.domain.execution import (
    DEFAULT_LIMITS,
    EXECUTION_CONTRACT_VERSION,
    ExecutionGateway,
    ExecutionLimits,
    ExecutionOutcome,
    ExecutionRequest,
)
from coursefuzz.domain.models import TestCase as CFTestCase

GATEWAY_FACTORIES = [
    pytest.param(LocalRestrictedRunner, id="local-restricted-runner"),
]

INCREMENT = "def solve(value):\n    return value + 1\n"


@pytest.fixture(params=GATEWAY_FACTORIES)
def gateway(request: pytest.FixtureRequest) -> ExecutionGateway:
    return request.param()


def _case(inputs: tuple[int, ...], expected: int) -> CFTestCase:
    return CFTestCase(inputs=inputs, expected=expected, label="probe", source="deterministic")


def _request(
    source: str,
    tests: tuple[CFTestCase, ...],
    *,
    limits: ExecutionLimits = DEFAULT_LIMITS,
    program_id: str = "candidate",
) -> ExecutionRequest:
    return ExecutionRequest.build(
        program_id=program_id,
        source=source,
        entrypoint="solve",
        tests=tests,
        limits=limits,
    )


def test_completed_execution_reports_typed_outputs_and_receipt(gateway: ExecutionGateway) -> None:
    request = _request(INCREMENT, (_case((1,), 2), _case((2,), 99)))

    result = gateway.execute(request)

    assert result.outcome is ExecutionOutcome.COMPLETED
    assert result.completed
    assert result.passed == 1
    assert result.failed == 1
    assert len(result.outputs) == 2
    assert result.outputs[0].actual == 2
    assert result.outputs[0].passed is True
    assert result.outputs[1].passed is False
    assert result.error is None

    receipt = result.receipt
    assert receipt.request_digest == request.digest
    assert receipt.runtime  # interpreter identity is pinned on every execution
    assert receipt.outcome is ExecutionOutcome.COMPLETED
    assert receipt.termination_reason == "completed"
    assert receipt.output_bytes > 0
    assert receipt.wall_ms >= 0


def test_rejected_source_never_runs(gateway: ExecutionGateway) -> None:
    request = _request("import os\ndef solve(value):\n    return value\n", (_case((1,), 1),))

    result = gateway.execute(request)

    assert result.outcome is ExecutionOutcome.REJECTED
    assert result.passed == 0
    assert result.failed == 1
    assert result.outputs == ()
    assert result.error is not None
    assert result.receipt.termination_reason == "restricted-language-violation"
    assert result.receipt.request_digest == request.digest


def test_wall_deadline_is_enforced_out_of_process(gateway: ExecutionGateway) -> None:
    tight = ExecutionLimits(wall_seconds=0.01, output_bytes=1_000_000, source_bytes=16_384)
    request = _request(INCREMENT, (_case((1,), 2),), limits=tight)

    result = gateway.execute(request)

    assert result.outcome is ExecutionOutcome.TIMED_OUT
    assert result.passed == 0
    assert result.failed == 1
    assert result.error == "Execution exceeded the total deadline"
    assert result.receipt.termination_reason == "wall-deadline-exceeded"


def test_request_digest_is_deterministic_and_source_sensitive() -> None:
    a = _request(INCREMENT, (_case((1,), 2),))
    b = _request(INCREMENT, (_case((1,), 2),))
    c = _request("def solve(value):\n    return value + 2\n", (_case((1,), 3),))

    assert a.digest == b.digest
    assert a.digest != c.digest
    assert a.source_sha256 == b.source_sha256
    assert a.source_sha256 != c.source_sha256


def test_contract_versions_are_pinned(gateway: ExecutionGateway) -> None:
    request = _request(INCREMENT, (_case((1,), 2),))

    result = gateway.execute(request)

    assert request.contract_version == EXECUTION_CONTRACT_VERSION
    assert result.receipt.contract_version == EXECUTION_CONTRACT_VERSION
    assert gateway.contract_version == EXECUTION_CONTRACT_VERSION
