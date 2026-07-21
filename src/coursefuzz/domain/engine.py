from __future__ import annotations

import hashlib
import itertools
import json
import re
import time

from coursefuzz.adapters.hypotheses import (
    HypothesisContext,
    HypothesisProvider,
    SurvivorHint,
)
from coursefuzz.adapters.sandbox import SubprocessPythonSandbox
from coursefuzz.domain.models import (
    AnalysisResult,
    AssignmentSpec,
    AttackHypothesis,
    CandidatePatch,
    GitHubPullRequestDestination,
    HypothesisVerdict,
    JsonAtom,
    MutationMetrics,
    OracleDecision,
    PatchTarget,
    ProgramVariant,
    SuiteExecution,
    TestCase,
)
from coursefuzz.domain.oracle import CompositeOracle


def bind_candidate_payload(candidate: CandidatePatch) -> CandidatePatch:
    payload = candidate.model_dump(
        mode="json",
        exclude={"id", "payload_sha256"},
    )
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(serialized).hexdigest()
    return candidate.model_copy(
        update={"id": f"patch-{digest[:12]}", "payload_sha256": digest}
    )


class AssessmentEngine:
    def __init__(
        self,
        sandbox: SubprocessPythonSandbox,
        hypotheses: HypothesisProvider,
        max_analysis_seconds: float = 30.0,
        oracle: CompositeOracle | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.hypotheses = hypotheses
        self.max_analysis_seconds = max_analysis_seconds
        self.oracle = oracle or CompositeOracle()

    def analyze(self, assignment: AssignmentSpec) -> AnalysisResult:
        deadline = time.monotonic() + self.max_analysis_seconds
        before, survivors = self._measure(
            assignment, assignment.instructor_tests, deadline
        )
        if not survivors:
            return AnalysisResult(
                before=before,
                projected_after=before,
                survivors_before=(),
                hypothesis_verdicts=(),
                evidence=self._evidence(assignment, (), finding=False),
            )

        proposed = self.hypotheses.propose(
            HypothesisContext.from_assignment(assignment),
            tuple(
                SurvivorHint(id=item.id, misconception=item.misconception)
                for item in survivors
            ),
        )
        verdicts = tuple(
            self._verify_hypothesis(assignment, survivors, item, deadline)
            for item in proposed
        )
        verified = [verdict for verdict in verdicts if verdict.status == "verified"]
        if not verified:
            return AnalysisResult(
                before=before,
                projected_after=before,
                survivors_before=tuple(item.id for item in survivors),
                hypothesis_verdicts=verdicts,
                evidence=self._evidence(assignment, verdicts, finding=False),
            )

        winner = max(
            verified,
            key=lambda item: (
                len(item.killed_mutants),
                tuple(-value for value in item.hypothesis.inputs),
            ),
        )
        target = self._program_by_id(survivors, winner.killed_mutants[0])
        minimized_inputs = self._minimize(assignment, target, deadline)
        decision = self._oracle_decision(assignment, minimized_inputs, deadline)
        if decision.expected is None:
            raise ValueError("Independent accepted solutions did not agree on the minimized input")
        minimized = TestCase(
            inputs=minimized_inputs,
            expected=decision.expected,
            label=f"CourseFuzz regression: {winner.hypothesis.misconception}",
            source="minimized",
        )
        killed_mutants: list[str] = []
        observed_actual: JsonAtom | None = None
        for survivor in survivors:
            execution = self._run_suite(
                survivor, assignment.entrypoint, (minimized,), deadline
            )
            if execution.all_passed:
                continue
            killed_mutants.append(survivor.id)
            if observed_actual is None and execution.outputs:
                output = execution.outputs[0]["actual"]
                if isinstance(output, (str, int, float, bool)):
                    observed_actual = output
        if not killed_mutants or observed_actual is None:
            raise ValueError("The minimized counterexample did not reproduce the disagreement")
        hardened_tests = (*assignment.instructor_tests, minimized)
        projected_after, _ = self._measure(assignment, hardened_tests, deadline)
        candidate = self._build_patch(
            minimized,
            tuple(killed_mutants),
            assignment.entrypoint,
            observed_actual,
            target.title,
            assignment,
            decision,
        )

        return AnalysisResult(
            before=before,
            projected_after=projected_after,
            survivors_before=tuple(item.id for item in survivors),
            hypothesis_verdicts=verdicts,
            candidate=candidate,
            evidence=self._evidence(assignment, verdicts, finding=True),
        )

    def verify_applied_patch(
        self, assignment: AssignmentSpec, candidate: CandidatePatch
    ) -> MutationMetrics:
        deadline = time.monotonic() + self.max_analysis_seconds
        metrics, _ = self._measure(
            assignment,
            (*assignment.instructor_tests, candidate.test),
            deadline,
        )
        return metrics

    def _measure(
        self,
        assignment: AssignmentSpec,
        tests: tuple[TestCase, ...] | list[TestCase],
        deadline: float,
    ) -> tuple[MutationMetrics, tuple[ProgramVariant, ...]]:
        # One batch covers every mutant and accepted control against the same suite. For the local
        # runner this is its own run_suite looped (identical behavior); for a container runner it is
        # a single sandbox start-up instead of one per program.
        programs = (*assignment.mutants, *assignment.accepted_solutions)
        executions = self._run_suite_batch(programs, assignment.entrypoint, tests, deadline)
        mutant_count = len(assignment.mutants)
        mutant_results = executions[:mutant_count]
        accepted_results = executions[mutant_count:]
        survivors: list[ProgramVariant] = [
            mutant
            for mutant, result in zip(assignment.mutants, mutant_results, strict=True)
            if result.all_passed
        ]
        killed = sum(1 for result in mutant_results if not result.all_passed)
        accepted_passed = sum(1 for result in accepted_results if result.all_passed)
        total = mutant_count
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
        deadline: float,
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
        expected = self._consensus_expected(assignment, hypothesis.inputs, deadline)
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
            result = self._run_suite(mutant, assignment.entrypoint, (test,), deadline)
            if not result.all_passed:
                killed.append(mutant.id)
                if result.outputs:
                    output = result.outputs[0].get("actual")
                    if isinstance(output, (str, int, float, bool)):
                        actual = output
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

    def _oracle_decision(
        self,
        assignment: AssignmentSpec,
        inputs: tuple[int, ...],
        deadline: float,
    ) -> OracleDecision:
        def probe(program: ProgramVariant) -> JsonAtom | None:
            case = TestCase(
                inputs=inputs, expected=None, label="oracle probe", source="deterministic"
            )
            execution = self._run_suite(program, assignment.entrypoint, (case,), deadline)
            if execution.error or not execution.outputs:
                return None
            output = execution.outputs[0]["actual"]
            return output if isinstance(output, (str, int, float, bool)) else None

        return self.oracle.decide(assignment, inputs, probe)

    def _consensus_expected(
        self,
        assignment: AssignmentSpec,
        inputs: tuple[int, ...],
        deadline: float,
    ) -> JsonAtom | None:
        return self._oracle_decision(assignment, inputs, deadline).expected

    def _minimize(
        self,
        assignment: AssignmentSpec,
        target: ProgramVariant,
        deadline: float,
    ) -> tuple[int, ...]:
        values = range(assignment.domain_min, assignment.domain_max + 1)
        candidates = sorted(
            itertools.product(values, repeat=len(assignment.input_names)),
            key=lambda item: (
                sum(abs(value) for value in item),
                max(abs(value) for value in item),
                item,
            ),
        )
        for inputs in candidates:
            expected = self._consensus_expected(assignment, inputs, deadline)
            if expected is None:
                continue
            test = TestCase(
                inputs=inputs,
                expected=expected,
                label="minimization probe",
                source="deterministic",
            )
            if not self._run_suite(
                target, assignment.entrypoint, (test,), deadline
            ).all_passed:
                return tuple(inputs)
        raise ValueError("Could not minimize the verified counterexample")

    def _run_suite(
        self,
        program: ProgramVariant,
        entrypoint: str,
        tests: tuple[TestCase, ...] | list[TestCase],
        deadline: float,
    ) -> SuiteExecution:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Analysis exceeded its total execution deadline")
        return self.sandbox.run_suite(
            program,
            entrypoint,
            tests,
            timeout_seconds=remaining,
        )

    def _run_suite_batch(
        self,
        programs: tuple[ProgramVariant, ...],
        entrypoint: str,
        tests: tuple[TestCase, ...] | list[TestCase],
        deadline: float,
    ) -> list[SuiteExecution]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Analysis exceeded its total execution deadline")
        return self.sandbox.run_suite_batch(
            programs,
            entrypoint,
            tests,
            timeout_seconds=remaining,
        )

    @staticmethod
    def _program_by_id(programs: tuple[ProgramVariant, ...], program_id: str) -> ProgramVariant:
        return next(program for program in programs if program.id == program_id)

    @staticmethod
    def _build_patch(
        test: TestCase,
        killed_mutants: tuple[str, ...],
        entrypoint: str,
        observed_actual: JsonAtom,
        target_title: str,
        assignment: AssignmentSpec,
        oracle: OracleDecision,
    ) -> CandidatePatch:
        case_key = json.dumps(
            {"inputs": list(test.inputs), "expected": test.expected},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        case_digest = hashlib.sha256(case_key).hexdigest()[:8]
        safe_entrypoint = re.sub(r"[^a-zA-Z0-9_]", "_", entrypoint).lower()
        pytest_source = (
            f"from solution import {entrypoint}\n\n\n"
            f"def test_coursefuzz_{safe_entrypoint}_{case_digest}():\n"
            f"    assert {entrypoint}{test.inputs!r} == {test.expected!r}\n"
        )
        target_path = (
            f"{assignment.destination.test_directory}/"
            f"test_coursefuzz_{safe_entrypoint}_{case_digest}.py"
        )
        if isinstance(assignment.destination, GitHubPullRequestDestination):
            target = PatchTarget(
                kind="github_pull_request",
                path=target_path,
                repository=assignment.destination.repository,
                base_branch=assignment.destination.base_branch,
            )
        else:
            target = PatchTarget(kind="local_artifact", path=target_path)
        candidate = CandidatePatch(
            id="pending",
            test=test,
            observed_actual=observed_actual,
            rationale=(
                f"Execution proved that '{target_title}' disagrees with every accepted control "
                f"on the minimized input {test.inputs}."
            ),
            target_mutants=killed_mutants,
            payload_sha256="pending",
            pytest_source=pytest_source,
            oracle=oracle,
            target=target,
        )
        return bind_candidate_payload(candidate)

    @staticmethod
    def _evidence(
        assignment: AssignmentSpec,
        verdicts: tuple[HypothesisVerdict, ...] | list[HypothesisVerdict],
        *,
        finding: bool,
    ) -> dict:
        return {
            "oracle": f"{len(assignment.accepted_solutions)} independently checked controls",
            "truth_source": "compiled restricted Python executions",
            "hypothesis_providers": sorted({item.hypothesis.provider for item in verdicts}),
            "search_domain": [assignment.domain_min, assignment.domain_max],
            "domain_cases": (assignment.domain_max - assignment.domain_min + 1)
            ** len(assignment.input_names),
            "gpt_decides_correctness": False,
            "finding": finding,
        }
