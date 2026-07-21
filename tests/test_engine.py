from coursefuzz.adapters.hypotheses import DeterministicHypothesisProvider
from coursefuzz.adapters.sandbox import SubprocessPythonSandbox
from coursefuzz.data.demo import TRIANGLE_ASSIGNMENT
from coursefuzz.domain.engine import AssessmentEngine


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
    assert len(rejected) == 2
    assert len(verified) == 2
