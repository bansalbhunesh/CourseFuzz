"""Tests for oracle provenance and the composite decision (Phase 3)."""

from __future__ import annotations

from collections.abc import Callable

from coursefuzz.adapters.hypotheses import DeterministicHypothesisProvider
from coursefuzz.adapters.sandbox import LocalRestrictedRunner
from coursefuzz.data.demo import TRIANGLE_ASSIGNMENT
from coursefuzz.domain.engine import AssessmentEngine
from coursefuzz.domain.models import JsonAtom, ProgramVariant
from coursefuzz.domain.oracle import CompositeOracle

A = TRIANGLE_ASSIGNMENT
NON_FIXTURE = (9, 9, 9)  # not among the seeded instructor tests
FIXTURE = (3, 3, 3)  # a seeded instructor fixture -> "equilateral"


def _probe(values: dict[str, JsonAtom | None]) -> Callable[[ProgramVariant], JsonAtom | None]:
    return lambda program: values.get(program.id)


def test_composite_resolves_with_provenance_when_sources_agree() -> None:
    decision = CompositeOracle().decide(
        A, NON_FIXTURE, _probe({p.id: "scalene" for p in A.accepted_solutions})
    )
    assert decision.resolved
    assert decision.expected == "scalene"
    assert "consensus" in decision.provenance and "reference" in decision.provenance
    assert decision.quorum >= 2


def test_composite_abstains_when_independent_sources_disagree() -> None:
    values = {p.id: "isosceles" for p in A.accepted_solutions}
    values[A.reference.id] = "scalene"  # reference disagrees with the other control
    decision = CompositeOracle().decide(A, NON_FIXTURE, _probe(values))
    assert not decision.resolved
    assert "disagreed" in (decision.abstention_reason or "")


def test_composite_abstains_when_a_control_fails_to_execute() -> None:
    decision = CompositeOracle().decide(A, NON_FIXTURE, _probe({A.reference.id: "scalene"}))
    assert not decision.resolved
    assert "execute" in (decision.abstention_reason or "")


def test_reviewed_fixture_conflicting_with_execution_abstains() -> None:
    # (3,3,3) is reviewed as "equilateral"; make execution claim "scalene".
    decision = CompositeOracle().decide(
        A, FIXTURE, _probe({p.id: "scalene" for p in A.accepted_solutions})
    )
    assert not decision.resolved
    assert "disagreed" in (decision.abstention_reason or "")


def test_engine_records_oracle_provenance_on_the_candidate() -> None:
    engine = AssessmentEngine(LocalRestrictedRunner(), DeterministicHypothesisProvider())

    result = engine.analyze(A)

    assert result.candidate is not None
    assert result.candidate.oracle is not None
    assert result.candidate.oracle.resolved
    assert result.candidate.oracle.quorum >= 1
    assert result.candidate.oracle.evidence_sources
