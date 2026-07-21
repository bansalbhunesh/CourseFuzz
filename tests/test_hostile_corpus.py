"""Hostile-program corpus for the local restricted runner.

Scope note (honest): this corpus locks the *current* containment surface, which is enforced at
the source-AST level plus an out-of-process wall deadline and output ceiling. It is NOT the
Milestone 1 exit gate. `next.md` requires that the abuse corpus eventually pass "without depending
on source-AST restrictions for containment" — that needs the no-network ``RemoteIsolatedRunner``.
When that adapter lands it must pass this same corpus (append its factory to ``RUNNERS``), at which
point the assertions can be tightened from "rejected before running" to "contained while running".

Until then, these tests guarantee we never silently widen the restricted language and let an
attack source execute.
"""

from __future__ import annotations

import pytest

from coursefuzz.adapters.sandbox import LocalRestrictedRunner
from coursefuzz.domain.execution import ExecutionGateway, ExecutionOutcome, ExecutionRequest
from coursefuzz.domain.models import TestCase as CFTestCase

RUNNERS = [
    pytest.param(LocalRestrictedRunner, id="local-restricted-runner"),
]

# name -> attack source. Each defines exactly one function named ``solve`` so it reaches the
# node allowlist rather than failing the shape check first, except where the violation *is* the
# point (oversized source is caught by the size gate).
HOSTILE_SOURCES: dict[str, str] = {
    "import_os": "import os\ndef solve(value):\n    return value\n",
    "import_socket": "import socket\ndef solve(value):\n    return value\n",
    "from_import_system": "from os import system\ndef solve(value):\n    return value\n",
    "call_open": "def solve(value):\n    return open('/etc/passwd')\n",
    "dunder_import": "def solve(value):\n    return __import__('os')\n",
    "attribute_escape": "def solve(value):\n    return value.__class__\n",
    "while_loop": "def solve(value):\n    while value:\n        return value\n    return value\n",
    "for_loop": "def solve(value):\n    for i in value:\n        return i\n",
    "list_comprehension": "def solve(value):\n    return [x for x in value]\n",
    "lambda_expression": "def solve(value):\n    return (lambda y: y)\n",
    "fstring_formatting": 'def solve(value):\n    return f"{value}"\n',
    "assignment_side_effect": "def solve(value):\n    x = value\n    return x\n",
    "global_statement": "def solve(value):\n    global sink\n    return value\n",
    "oversized_source": "def solve(value):\n    return value  # " + "A" * 17_000 + "\n",
}


def _request(source: str) -> ExecutionRequest:
    probe = CFTestCase(inputs=(1,), expected=1, label="probe", source="deterministic")
    return ExecutionRequest.build(
        program_id="hostile",
        source=source,
        entrypoint="solve",
        tests=(probe,),
    )


@pytest.mark.parametrize("runner_factory", RUNNERS)
@pytest.mark.parametrize("name", sorted(HOSTILE_SOURCES))
def test_hostile_source_is_contained(name: str, runner_factory: type[ExecutionGateway]) -> None:
    gateway = runner_factory()

    result = gateway.execute(_request(HOSTILE_SOURCES[name]))

    # The universal containment property: the attack never runs to a passing completion.
    assert result.outcome is not ExecutionOutcome.COMPLETED
    assert result.passed == 0
    assert result.error is not None
    # Every execution — even a rejected one — carries a runtime-pinned receipt.
    assert result.receipt.runtime


@pytest.mark.parametrize("runner_factory", RUNNERS)
@pytest.mark.parametrize("name", sorted(HOSTILE_SOURCES))
def test_hostile_source_is_rejected_before_running(
    name: str, runner_factory: type[ExecutionGateway]
) -> None:
    gateway = runner_factory()

    result = gateway.execute(_request(HOSTILE_SOURCES[name]))

    # For the current AST + size boundary, containment means rejection before execution.
    assert result.outcome is ExecutionOutcome.REJECTED
    assert result.receipt.termination_reason == "restricted-language-violation"
