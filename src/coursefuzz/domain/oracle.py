"""Oracle provenance: how the expected output for an input is established, with abstention.

The engine must never fabricate an expected value. This module makes that explicit: independent
sources (the instructor reference, the independently authored accepted controls, and reviewed
instructor fixtures) each read the expected output for one input, and a composite resolves it only
when every available source agrees — recording which sources agreed — or abstains with a reason.

Each source executes at most the programs it owns via ``probe``, so the composite runs the reference
once and each non-reference control once: the same executions the previous consensus performed, now
cross-checked against reviewed fixtures and carrying provenance.

Not built here: ``PropertyOracle`` needs assignment-declared invariants (Phase 4's schema); adding
an always-abstaining one now would be dead code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from coursefuzz.domain.models import AssignmentSpec, JsonAtom, OracleDecision, ProgramVariant

# Runs one program on the input under evaluation and returns its atom output, or None on failure.
ProgramProbe = Callable[[ProgramVariant], "JsonAtom | None"]


@dataclass(frozen=True)
class OracleReading:
    source_name: str
    status: Literal["resolved", "unavailable", "error"]
    value: JsonAtom | None = None
    evidence: tuple[str, ...] = field(default_factory=tuple)
    detail: str | None = None


class Oracle(ABC):
    name: str

    @abstractmethod
    def evaluate(
        self, assignment: AssignmentSpec, inputs: tuple[int, ...], probe: ProgramProbe
    ) -> OracleReading:
        raise NotImplementedError


class ReferenceOracle(Oracle):
    """The instructor-owned reference program's output."""

    name = "reference"

    def evaluate(
        self, assignment: AssignmentSpec, inputs: tuple[int, ...], probe: ProgramProbe
    ) -> OracleReading:
        value = probe(assignment.reference)
        if value is None:
            return OracleReading(
                self.name, "error", detail="the instructor reference failed to execute"
            )
        return OracleReading(self.name, "resolved", value=value, evidence=("reference",))


class ConsensusOracle(Oracle):
    """Independently authored accepted controls (excluding the reference) must agree."""

    name = "consensus"

    def evaluate(
        self, assignment: AssignmentSpec, inputs: tuple[int, ...], probe: ProgramProbe
    ) -> OracleReading:
        controls = [
            control
            for control in assignment.accepted_solutions
            if control.id != assignment.reference.id
        ]
        if not controls:
            return OracleReading(self.name, "unavailable")
        outputs: list[tuple[str, JsonAtom]] = []
        for control in controls:
            value = probe(control)
            if value is None:
                return OracleReading(
                    self.name, "error", detail="an accepted control failed to execute"
                )
            outputs.append((control.id, value))
        if len({value for _, value in outputs}) != 1:
            return OracleReading(
                self.name, "error", detail="independent accepted controls disagreed"
            )
        return OracleReading(
            self.name,
            "resolved",
            value=outputs[0][1],
            evidence=tuple(f"control:{cid}" for cid, _ in outputs),
        )


class FixtureOracle(Oracle):
    """A reviewed instructor input/output case is authoritative when it matches the input."""

    name = "fixture"

    def evaluate(
        self, assignment: AssignmentSpec, inputs: tuple[int, ...], probe: ProgramProbe
    ) -> OracleReading:
        for test in assignment.instructor_tests:
            if test.inputs == inputs and test.expected is not None:
                return OracleReading(
                    self.name, "resolved", value=test.expected, evidence=(f"fixture:{test.label}",)
                )
        return OracleReading(self.name, "unavailable")


class CompositeOracle:
    """Resolve the expected output only when every available source agrees, else abstain."""

    def __init__(self, oracles: Sequence[Oracle] | None = None) -> None:
        self.oracles: tuple[Oracle, ...] = (
            tuple(oracles)
            if oracles is not None
            else (ReferenceOracle(), ConsensusOracle(), FixtureOracle())
        )

    def decide(
        self, assignment: AssignmentSpec, inputs: tuple[int, ...], probe: ProgramProbe
    ) -> OracleDecision:
        readings = [oracle.evaluate(assignment, inputs, probe) for oracle in self.oracles]

        error = next((reading for reading in readings if reading.status == "error"), None)
        if error is not None:
            return OracleDecision(
                decision="abstained",
                provenance="composite",
                abstention_reason=error.detail or "a truth source failed to execute",
            )

        resolved = [reading for reading in readings if reading.status == "resolved"]
        if not resolved:
            return OracleDecision(
                decision="abstained",
                provenance="composite",
                abstention_reason="no independent source resolved the expected output",
            )

        evidence = tuple(item for reading in resolved for item in reading.evidence)
        if len({reading.value for reading in resolved}) != 1:
            return OracleDecision(
                decision="abstained",
                provenance="disagreement",
                evidence_sources=evidence,
                abstention_reason="independent truth sources disagreed on the expected output",
            )

        provenance = "+".join(sorted({reading.source_name for reading in resolved}))
        return OracleDecision(
            expected=resolved[0].value,
            decision="resolved",
            provenance=provenance,
            evidence_sources=evidence,
            quorum=len(evidence),
        )
