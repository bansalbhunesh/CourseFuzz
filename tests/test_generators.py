"""Tests for composable candidate generation and shared execution accounting (Milestone 4)."""

from __future__ import annotations

from coursefuzz.adapters.generators import (
    BoundaryGenerator,
    GeneratorScheduler,
    PermutationGenerator,
    deterministic_scheduler,
)
from coursefuzz.adapters.hypotheses import (
    DeterministicHypothesisProvider,
    ExistingTestView,
    HypothesisContext,
)
from coursefuzz.adapters.ledger import ExecutionLedger
from coursefuzz.adapters.sandbox import SubprocessPythonSandbox
from coursefuzz.data.demo import TRIANGLE_ASSIGNMENT
from coursefuzz.domain.engine import AssessmentEngine

A = TRIANGLE_ASSIGNMENT
CTX = HypothesisContext.from_assignment(A)


def test_scheduler_reproduces_the_deterministic_provider_candidate_for_candidate() -> None:
    scheduled = deterministic_scheduler().propose(CTX, ())
    baseline = DeterministicHypothesisProvider().propose(CTX, ())

    assert [h.inputs for h in scheduled] == [h.inputs for h in baseline]
    assert [h.misconception for h in scheduled] == [h.misconception for h in baseline]


def test_scheduler_tags_every_candidate_with_its_generator() -> None:
    proposed = deterministic_scheduler().propose(CTX, ())

    assert proposed
    assert all(h.generator in {"permutation", "boundary"} for h in proposed)


def test_scheduler_draws_from_multiple_generators_under_one_budget() -> None:
    # One instructor case (2 inputs) yields a single permutation; the boundary generator then fills
    # the remaining budget, so both generators appear in the one shared, deduplicated selection.
    context = HypothesisContext(
        title="pair",
        summary="two bounded integers",
        input_names=("a", "b"),
        domain_min=-1,
        domain_max=1,
        existing_tests=(ExistingTestView(inputs=(1, -1), label="mixed"),),
    )

    proposed = deterministic_scheduler().propose(context, ())

    assert proposed[0].generator == "permutation"  # permutations run first
    assert {h.generator for h in proposed} == {"permutation", "boundary"}
    inputs = [h.inputs for h in proposed]
    assert len(inputs) == len(set(inputs))


def test_scheduler_deduplicates_globally_and_respects_the_budget() -> None:
    proposed = deterministic_scheduler(budget=5).propose(CTX, ())

    inputs = [h.inputs for h in proposed]
    assert len(proposed) <= 5
    assert len(inputs) == len(set(inputs))  # no input proposed twice across generators
    existing = {test.inputs for test in A.instructor_tests}
    assert not (set(inputs) & existing)  # never re-proposes an instructor case


def test_a_single_generator_can_be_scheduled_alone() -> None:
    boundary_only = GeneratorScheduler((BoundaryGenerator(),)).propose(CTX, ())
    permutation_only = GeneratorScheduler((PermutationGenerator(),)).propose(CTX, ())

    assert all(h.generator == "boundary" for h in boundary_only)
    assert all(h.generator == "permutation" for h in permutation_only)


def test_generator_provenance_flows_through_the_engine_to_the_verdicts() -> None:
    engine = AssessmentEngine(SubprocessPythonSandbox(), deterministic_scheduler())

    result = engine.analyze(A)

    assert result.candidate is not None  # composition preserves the finding
    assert result.hypothesis_verdicts
    generators = {verdict.hypothesis.generator for verdict in result.hypothesis_verdicts}
    assert generators <= {"permutation", "boundary"}
    assert None not in generators  # every proposed hypothesis is attributed


def test_execution_ledger_charges_every_sandbox_call_and_is_deterministic() -> None:
    ledger = ExecutionLedger(SubprocessPythonSandbox())
    engine = AssessmentEngine(ledger, DeterministicHypothesisProvider())

    first = engine.analyze(A)
    calls_first, programs_first = ledger.suite_calls, ledger.programs_executed
    ledger.reset()
    second = engine.analyze(A)

    assert first.candidate is not None and second.candidate is not None
    assert calls_first > 0 and programs_first >= calls_first
    # Deterministic pipeline: the same analysis charges the same execution budget every time.
    assert (ledger.suite_calls, ledger.programs_executed) == (calls_first, programs_first)
