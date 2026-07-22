import sys
import types

import pytest

from coursefuzz.adapters.hypotheses import DeterministicHypothesisProvider
from coursefuzz.adapters.sandbox import SubprocessPythonSandbox
from coursefuzz.data.demo import TRIANGLE_ASSIGNMENT
from coursefuzz.domain.engine import AssessmentEngine
from coursefuzz.domain.models import AssignmentSpec, ProgramVariant
from coursefuzz.domain.models import TestCase as DomainTestCase


def test_engine_finds_minimal_verified_counterexample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    assert result.candidate.pytest_source.startswith(
        "from solution import classify_triangle\n"
    )
    accepted_module = types.ModuleType("solution")
    exec(TRIANGLE_ASSIGNMENT.reference.source, accepted_module.__dict__)
    monkeypatch.setitem(sys.modules, "solution", accepted_module)
    accepted_patch: dict[str, object] = {}
    exec(result.candidate.pytest_source, accepted_patch)
    generated_test = next(
        value
        for name, value in accepted_patch.items()
        if name.startswith("test_coursefuzz_")
    )
    assert callable(generated_test)
    generated_test()

    wrong_module = types.ModuleType("solution")
    wrong_source = next(
        mutant.source
        for mutant in TRIANGLE_ASSIGNMENT.mutants
        if mutant.id == "mutant-ab-only"
    )
    exec(wrong_source, wrong_module.__dict__)
    monkeypatch.setitem(sys.modules, "solution", wrong_module)
    wrong_patch: dict[str, object] = {}
    exec(result.candidate.pytest_source, wrong_patch)
    wrong_test = next(
        value
        for name, value in wrong_patch.items()
        if name.startswith("test_coursefuzz_")
    )
    assert callable(wrong_test)
    with pytest.raises(AssertionError):
        wrong_test()
    assert result.evidence["gpt_decides_correctness"] is False


def test_candidate_selection_prefers_maximum_coverage_over_a_smaller_input() -> None:
    """The counterexample is chosen to discriminate the MOST survivors, not to be smallest.

    Distilled from the maximum-pair benchmark case, where the old "minimize one winner toward its
    smallest input" path shed coverage. Two mutants survive the instructor suite:
      - ``returns-left`` is wrong whenever ``right > left``.
      - ``zero-fallback`` returns 0 when ``left < right`` -> wrong only if the true max is nonzero.
    The smallest divergent input ``(-1, 0)`` kills only ``returns-left`` (the max there is 0, which
    ``zero-fallback`` returns correctly). A slightly larger input ``(0, 1)`` kills both. Candidate
    selection must pick ``(0, 1)`` and catch both wrong programs with a single regression test.
    """

    reference = ProgramVariant(
        id="ref-max",
        title="Maximum of a pair",
        misconception="none",
        source=(
            "def maximum_pair(left, right):\n"
            "    if left >= right:\n"
            "        return left\n"
            "    return right\n"
        ),
    )
    control = ProgramVariant(
        id="control-max",
        title="Maximum of a pair, independently authored",
        misconception="none",
        source=(
            "def maximum_pair(left, right):\n"
            "    if right > left:\n"
            "        return right\n"
            "    return left\n"
        ),
    )
    assignment = AssignmentSpec(
        id="max-pair-coverage",
        title="Maximum of a pair",
        summary="Return the larger of two bounded integers, coverage-preservation probe.",
        entrypoint="maximum_pair",
        input_names=("left", "right"),
        domain_min=-1,
        domain_max=1,
        reference=reference,
        accepted_solutions=(reference, control),
        mutants=(
            ProgramVariant(
                id="returns-left",
                title="Returns the first input",
                misconception="Assumes the first input is larger.",
                source="def maximum_pair(left, right):\n    return left\n",
            ),
            ProgramVariant(
                id="zero-fallback",
                title="Uses zero when the second input is larger",
                misconception="Falls back to zero instead of the larger value.",
                source=(
                    "def maximum_pair(left, right):\n"
                    "    if left >= right:\n"
                    "        return left\n"
                    "    return 0\n"
                ),
            ),
        ),
        instructor_tests=(
            DomainTestCase(inputs=(1, -1), expected=1, label="left larger", source="instructor"),
        ),
    )
    engine = AssessmentEngine(SubprocessPythonSandbox(), DeterministicHypothesisProvider())

    result = engine.analyze(assignment)

    assert result.before.surviving_mutants == 2
    assert result.candidate is not None
    # One maximum-coverage candidate catches BOTH survivors; a smaller input would miss one.
    assert result.candidate.test.inputs == (0, 1)
    assert set(result.candidate.target_mutants) == {"returns-left", "zero-fallback"}
    assert result.projected_after.mutation_score == 100.0


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


class _CountingRunner(SubprocessPythonSandbox):
    def __init__(self) -> None:
        super().__init__()
        self.batch_calls = 0

    def run_suite_batch(self, programs, entrypoint, tests, timeout_seconds=None):  # type: ignore[override]
        self.batch_calls += 1
        return super().run_suite_batch(programs, entrypoint, tests, timeout_seconds)


def test_engine_measures_mutants_and_controls_in_one_batch() -> None:
    runner = _CountingRunner()
    engine = AssessmentEngine(runner, DeterministicHypothesisProvider())

    result = engine.analyze(TRIANGLE_ASSIGNMENT)

    # Baseline, accepted-control hypotheses, survivor hypotheses, and projected suite are four
    # bounded waves. The count is independent of candidate or domain size.
    assert runner.batch_calls == 4
    assert result.candidate is not None  # behavior is unchanged: it still finds the counterexample


def test_engine_enforces_a_total_analysis_deadline() -> None:
    engine = AssessmentEngine(
        SubprocessPythonSandbox(),
        DeterministicHypothesisProvider(),
        max_analysis_seconds=0,
    )

    with pytest.raises(TimeoutError, match="total execution deadline"):
        engine.analyze(TRIANGLE_ASSIGNMENT)
