import pytest

from coursefuzz.adapters.hypotheses import DeterministicHypothesisProvider
from coursefuzz.adapters.sandbox import SubprocessPythonSandbox
from coursefuzz.data.demo import TRIANGLE_ASSIGNMENT
from coursefuzz.domain.engine import AssessmentEngine
from coursefuzz.domain.models import AssignmentSpec, ProgramVariant
from coursefuzz.domain.models import TestCase as DomainTestCase


def test_engine_finds_minimal_verified_counterexample() -> None:
    engine = AssessmentEngine(SubprocessPythonSandbox(), DeterministicHypothesisProvider())

    result = engine.analyze(TRIANGLE_ASSIGNMENT)

    assert result.before.mutation_score == 62.5
    assert result.projected_after.mutation_score == 100.0
    assert result.projected_after.accepted_solution_pass_rate == 100.0
    assert result.candidate.test.inputs == (1, 2, 2)
    assert result.candidate.test.expected == "isosceles"
    assert result.candidate.observed_actual == "scalene"
    assert set(result.candidate.target_mutants) == {
        "mutant-ab-only",
        "mutant-no-bc-pair",
        "mutant-guarded-bc",
    }
    assert result.evidence["gpt_decides_correctness"] is False


def test_hypotheses_that_do_not_diverge_are_rejected() -> None:
    engine = AssessmentEngine(SubprocessPythonSandbox(), DeterministicHypothesisProvider())

    result = engine.analyze(TRIANGLE_ASSIGNMENT)

    rejected = [item for item in result.hypothesis_verdicts if item.status == "rejected"]
    verified = [item for item in result.hypothesis_verdicts if item.status == "verified"]
    assert rejected
    assert verified
    assert len(rejected) + len(verified) <= 8


def test_engine_returns_no_action_when_the_supplied_suite_kills_every_mutant() -> None:
    engine = AssessmentEngine(SubprocessPythonSandbox(), DeterministicHypothesisProvider())
    complete = TRIANGLE_ASSIGNMENT.model_copy(
        update={
            "instructor_tests": (
                *TRIANGLE_ASSIGNMENT.instructor_tests,
                DomainTestCase(
                    inputs=(1, 2, 2),
                    expected="isosceles",
                    label="last equal pair",
                    source="instructor",
                ),
            )
        }
    )

    result = engine.analyze(complete)

    assert result.before.mutation_score == 100.0
    assert result.candidate is None
    assert result.evidence["finding"] is False


def test_engine_is_not_tied_to_the_seeded_triangle_assignment() -> None:
    reference = ProgramVariant(
        id="reference-absolute",
        title="Reference absolute value",
        misconception="none",
        source=(
            "def absolute_value(n):\n"
            "    if n < 0:\n"
            "        return -n\n"
            "    return n\n"
        ),
    )
    alternative = ProgramVariant(
        id="accepted-absolute",
        title="Accepted absolute value",
        misconception="none",
        source=(
            "def absolute_value(n):\n"
            "    if n >= 0:\n"
            "        return n\n"
            "    return 0 - n\n"
        ),
    )
    assignment = AssignmentSpec(
        id="absolute-value",
        title="Absolute value",
        summary="Return the non-negative magnitude of one bounded integer input.",
        entrypoint="absolute_value",
        input_names=("n",),
        domain_min=-3,
        domain_max=3,
        reference=reference,
        accepted_solutions=(reference, alternative),
        mutants=(
            ProgramVariant(
                id="mutant-negates-everything",
                title="Negates every input",
                misconception="Absolute value always means negation.",
                source="def absolute_value(n):\n    return -n\n",
            ),
            ProgramVariant(
                id="mutant-identity",
                title="Returns input unchanged",
                misconception="Negative inputs are already magnitudes.",
                source="def absolute_value(n):\n    return n\n",
            ),
        ),
        instructor_tests=(
            DomainTestCase(
                inputs=(-2,), expected=2, label="negative", source="instructor"
            ),
            DomainTestCase(inputs=(0,), expected=0, label="zero", source="instructor"),
        ),
    )
    engine = AssessmentEngine(SubprocessPythonSandbox(), DeterministicHypothesisProvider())

    result = engine.analyze(assignment)

    assert result.before.mutation_score == 50.0
    assert result.projected_after.mutation_score == 100.0
    assert result.candidate is not None
    assert result.candidate.test.inputs == (1,)
    assert result.candidate.test.expected == 1
    assert result.candidate.observed_actual == -1
    assert "absolute_value" in result.candidate.pytest_source


def test_engine_enforces_a_total_analysis_deadline() -> None:
    engine = AssessmentEngine(
        SubprocessPythonSandbox(),
        DeterministicHypothesisProvider(),
        max_analysis_seconds=0,
    )

    with pytest.raises(TimeoutError, match="total execution deadline"):
        engine.analyze(TRIANGLE_ASSIGNMENT)
