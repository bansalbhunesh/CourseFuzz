"""Shared execution accounting (roadmap Milestone 4).

Any honest "better than random at equal budget" comparison has to charge every execution to one
ledger. :class:`ExecutionLedger` wraps a sandbox and counts sandbox invocations and the number of
programs run, delegating behavior unchanged. It is a drop-in for the engine's ``sandbox`` because it
forwards the exact ``run_suite`` / ``run_suite_batch`` surface the engine uses.
"""

from __future__ import annotations

from collections.abc import Sequence

from coursefuzz.domain.models import ProgramVariant, SuiteExecution, TestCase


class ExecutionLedger:
    """Wrap a sandbox and charge every execution to one shared counter.

    ``suite_calls`` counts sandbox round-trips (one per ``run_suite``; one per ``run_suite_batch``,
    regardless of batch size). ``programs_executed`` counts individual programs run, so a batch of N
    is charged as N program-executions but one round-trip — the two numbers a budget comparison and
    a container-vs-local cost analysis both need.
    """

    def __init__(self, sandbox: object) -> None:
        self._sandbox = sandbox
        self.suite_calls = 0
        self.programs_executed = 0

    def run_suite(
        self,
        program: ProgramVariant,
        entrypoint: str,
        tests: tuple[TestCase, ...] | list[TestCase],
        timeout_seconds: float | None = None,
    ) -> SuiteExecution:
        self.suite_calls += 1
        self.programs_executed += 1
        return self._sandbox.run_suite(program, entrypoint, tests, timeout_seconds)

    def run_suite_batch(
        self,
        programs: Sequence[ProgramVariant],
        entrypoint: str,
        tests: tuple[TestCase, ...] | list[TestCase],
        timeout_seconds: float | None = None,
    ) -> list[SuiteExecution]:
        programs = tuple(programs)
        self.suite_calls += 1
        self.programs_executed += len(programs)
        return self._sandbox.run_suite_batch(programs, entrypoint, tests, timeout_seconds)

    def reset(self) -> None:
        self.suite_calls = 0
        self.programs_executed = 0
