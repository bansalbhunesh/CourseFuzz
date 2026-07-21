from __future__ import annotations

import hashlib
import itertools
import json

from coursefuzz.adapters.hypotheses import HypothesisProvider
from coursefuzz.adapters.sandbox import SubprocessPythonSandbox
from coursefuzz.domain.models import (
    AnalysisResult,
    AssignmentSpec,
    AttackHypothesis,
    CandidatePatch,
    HypothesisVerdict,
    MutationMetrics,
    ProgramVariant,
    TestCase,
)


class AssessmentEngine:
    def __init__(
        self,
        sandbox: SubprocessPythonSandbox,
        hypotheses: HypothesisProvider,
    ) -> None:
        self.sandbox = sandbox
        self.hypotheses = hypotheses

    def analyze(self, assignment: AssignmentSpec) -> AnalysisResult:
        before, survivors = self._measure(assignment, assignment.instructor_tests)
        if not survivors:
            raise ValueError("The instructor suite already kills every non-equivalent mutant")

        proposed = self.hypotheses.propose(assignment, survivors)
        verdicts = tuple(self._verify_hypothesis(assignment, survivors, item) for item in proposed)
        verified = [verdict for verdict in verdicts if verdict.status == "verified"]
        if not verified:
            raise ValueError("No proposed hypothesis survived execution-backed verification")

        winner = max(
            verified,
            key=lambda item: (
                len(item.killed_mutants),
                tuple(-value for value in item.hypothesis.inputs),
            ),
        )
        target = self._program_by_id(survivors, winner.killed_mutants[0])
        minimized_inputs = self._minimize(assignment, target)
        expected = self._consensus_expected(assignment, minimized_inputs)
        if expected is None:
            raise ValueError("Independent accepted solutions did not agree on the minimized input")
        minimized = TestCase(
            inputs=minimized_inputs,
            expected=expected,
            label="CourseFuzz regression: isosceles permutation",
            source="minimized",
        )
        minimized_execution = self.sandbox.run_suite(
            target, assignment.entrypoint, (minimized,)
        )
        if not minimized_execution.outputs:
            raise ValueError("The minimized counterexample did not produce observable output")
        observed_actual = str(minimized_execution.outputs[0]["actual"])
        hardened_tests = (*assignment.instructor_tests, minimized)
        projected_after, _ = self._measure(assignment, hardened_tests)
        candidate = self._build_patch(
            minimized, winner, assignment.entrypoint, observed_actual
        )

        return AnalysisResult(
            before=before,
            projected_after=projected_after,
            survivors_before=tuple(item.id for item in survivors),
            hypothesis_verdicts=verdicts,
            candidate=candidate,
            evidence={
                "oracle": "two independently authored accepted solutions",
                "truth_source": "compiled restricted Python executions",
                "hypothesis_providers": sorted({item.hypothesis.provider for item in verdicts}),
                "search_domain": [assignment.domain_min, assignment.domain_max],
                "domain_cases": (assignment.domain_max - assignment.domain_min + 1)
                ** len(assignment.input_names),
                "gpt_decides_correctness": False,
            },
        )

    def verify_applied_patch(
        self, assignment: AssignmentSpec, candidate: CandidatePatch
    ) -> MutationMetrics:
        metrics, _ = self._measure(assignment, (*assignment.instructor_tests, candidate.test))
        return metrics

    def _measure(
        self, assignment: AssignmentSpec, tests: tuple[TestCase, ...] | list[TestCase]
    ) -> tuple[MutationMetrics, tuple[ProgramVariant, ...]]:
        survivors: list[ProgramVariant] = []
        killed = 0
        for mutant in assignment.mutants:
            result = self.sandbox.run_suite(mutant, assignment.entrypoint, tests)
            if result.all_passed:
                survivors.append(mutant)
            else:
                killed += 1

        accepted_passed = sum(
            self.sandbox.run_suite(solution, assignment.entrypoint, tests).all_passed
            for solution in assignment.accepted_solutions
        )
        total = len(assignment.mutants)
        metrics = MutationMetrics(
            total_mutants=total,
            killed_mutants=killed,
            surviving_mutants=len(survivors),
            mutation_score=round((killed / total) * 100, 1) if total else 100.0,
            accepted_solution_pass_rate=round(
                (accepted_passed / len(assignment.accepted_solutions)) * 100, 1
            ),
        )
        return metrics, tuple(survivors)

    def _verify_hypothesis(
        self,
        assignment: AssignmentSpec,
        survivors: tuple[ProgramVariant, ...],
        hypothesis: AttackHypothesis,
    ) -> HypothesisVerdict:
        if len(hypothesis.inputs) != len(assignment.input_names):
            return HypothesisVerdict(
                hypothesis=hypothesis,
                status="rejected",
                reason="Input arity does not match the assignment signature.",
            )
        if any(
            value < assignment.domain_min or value > assignment.domain_max
            for value in hypothesis.inputs
        ):
            return HypothesisVerdict(
                hypothesis=hypothesis,
                status="rejected",
                reason="Input is outside the declared bounded domain.",
            )
        expected = self._consensus_expected(assignment, hypothesis.inputs)
        if expected is None:
            return HypothesisVerdict(
                hypothesis=hypothesis,
                status="rejected",
                reason="Independent accepted solutions disagreed; the oracle abstained.",
            )
        test = TestCase(
            inputs=hypothesis.inputs,
            expected=expected,
            label="candidate",
            source="gpt-5.6" if hypothesis.provider == "gpt-5.6" else "deterministic",
        )
        killed: list[str] = []
        actual = expected
        for mutant in survivors:
            result = self.sandbox.run_suite(mutant, assignment.entrypoint, (test,))
            if not result.all_passed:
                killed.append(mutant.id)
                if result.outputs:
                    actual = str(result.outputs[0].get("actual"))
        if not killed:
            return HypothesisVerdict(
                hypothesis=hypothesis,
                status="rejected",
                reason="Execution found no behavioral divergence.",
                expected=expected,
            )
        return HypothesisVerdict(
            hypothesis=hypothesis,
            status="verified",
            reason="Execution reproduced a real output disagreement.",
            expected=expected,
            actual=actual,
            killed_mutants=tuple(killed),
        )

    def _consensus_expected(
        self, assignment: AssignmentSpec, inputs: tuple[int, ...]
    ) -> str | None:
        probe = TestCase(
            inputs=inputs,
            expected=None,
            label="oracle probe",
            source="deterministic",
        )
        outputs: list[str] = []
        for solution in assignment.accepted_solutions:
            execution = self.sandbox.run_suite(solution, assignment.entrypoint, (probe,))
            if execution.error or not execution.outputs:
                return None
            outputs.append(str(execution.outputs[0]["actual"]))
        return outputs[0] if len(set(outputs)) == 1 else None

    def _minimize(self, assignment: AssignmentSpec, target: ProgramVariant) -> tuple[int, ...]:
        values = range(max(1, assignment.domain_min), assignment.domain_max + 1)
        candidates = sorted(
            itertools.product(values, repeat=len(assignment.input_names)),
            key=lambda item: (max(item), sum(item), item),
        )
        for inputs in candidates:
            expected = self._consensus_expected(assignment, inputs)
            if expected is None:
                continue
            test = TestCase(
                inputs=inputs,
                expected=expected,
                label="minimization probe",
                source="deterministic",
            )
            if not self.sandbox.run_suite(target, assignment.entrypoint, (test,)).all_passed:
                return tuple(inputs)
        raise ValueError("Could not minimize the verified counterexample")

    @staticmethod
    def _program_by_id(programs: tuple[ProgramVariant, ...], program_id: str) -> ProgramVariant:
        return next(program for program in programs if program.id == program_id)

    @staticmethod
    def _build_patch(
        test: TestCase,
        verdict: HypothesisVerdict,
        entrypoint: str,
        observed_actual: str,
    ) -> CandidatePatch:
        pytest_source = (
            "def test_coursefuzz_isosceles_permutation():\n"
            f"    assert {entrypoint}{test.inputs!r} == {test.expected!r}\n"
        )
        payload = {
            "inputs": list(test.inputs),
            "expected": test.expected,
            "pytest_source": pytest_source,
            "target_mutants": list(verdict.killed_mutants),
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(serialized).hexdigest()
        return CandidatePatch(
            id=f"patch-{digest[:12]}",
            test=test,
            observed_actual=observed_actual,
            rationale=(
                "The instructor suite checks only a=b. This minimized permutation proves that "
                "a different equal-side position is graded incorrectly."
            ),
            target_mutants=verdict.killed_mutants,
            payload_sha256=digest,
            pytest_source=pytest_source,
        )
