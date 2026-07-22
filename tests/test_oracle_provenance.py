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


def test_finding_evidence_surfaces_resolved_oracle_provenance() -> None:
    """A finding's evidence must show HOW the expected output was established (audit + UI)."""
    engine = AssessmentEngine(LocalRestrictedRunner(), DeterministicHypothesisProvider())

    result = engine.analyze(A)

    oracle_evidence = result.evidence["oracle_evidence"]
    assert oracle_evidence["decision"] == "resolved"
    assert oracle_evidence["provenance"]  # e.g. "consensus+fixture+reference"
    assert oracle_evidence["sources"]
    assert oracle_evidence["quorum"] >= 1


def test_abstention_evidence_surfaces_the_reason() -> None:
    """When the oracle abstains, "no finding" must carry the reason, not be a silent black box."""
    from coursefuzz.domain.models import ProgramVariant
    from coursefuzz.domain.models import TestCase as CFTestCase

    # Two accepted controls that disagree exactly where the surviving mutant diverges (negatives).
    control_a = ProgramVariant(
        id="control-a",
        title="Clamps negatives to zero",
        misconception="none",
        source="def f(n):\n    if n < 0:\n        return 0\n    return n\n",
    )
    control_b = ProgramVariant(
        id="control-b",
        title="Clamps negatives to one",
        misconception="none",
        source="def f(n):\n    if n < 0:\n        return 1\n    return n\n",
    )
    assignment = A.model_copy(
        update={
            "id": "poisoned",
            "entrypoint": "f",
            "input_names": ("n",),
            "domain_min": -3,
            "domain_max": 3,
            "reference": control_a,
            "accepted_solutions": (control_a, control_b),
            "mutants": (
                ProgramVariant(
                    id="mutant-identity",
                    title="Identity",
                    misconception="assumes identity",
                    source="def f(n):\n    return n\n",
                ),
            ),
            "instructor_tests": (
                CFTestCase(inputs=(2,), expected=2, label="positive", source="instructor"),
                CFTestCase(inputs=(0,), expected=0, label="zero", source="instructor"),
            ),
        }
    )
    engine = AssessmentEngine(LocalRestrictedRunner(), DeterministicHypothesisProvider())

    result = engine.analyze(assignment)

    assert result.candidate is None
    oracle_evidence = result.evidence["oracle_evidence"]
    assert oracle_evidence["decision"] == "abstained"
    assert oracle_evidence["abstention_reasons"]
    assert any("abstain" in reason.lower() for reason in oracle_evidence["abstention_reasons"])


def test_audit_trail_records_oracle_provenance(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The persisted audit event for a finding carries how the expected output was established."""
    from coursefuzz.main import create_app

    app = create_app(tmp_path / "coursefuzz.db", tmp_path / "artifacts")
    service = app.state.run_service
    run, _ = service.create_run("triangle-classifier", "provenance-audit")

    service.analyze_run(run.id)

    events = {event.event_type: event for event in service.repository.events_after(run.id)}
    oracle = events["analysis.verified"].payload["oracle"]
    assert oracle["decision"] == "resolved"
    assert oracle["provenance"]
    assert oracle["quorum"] >= 1
