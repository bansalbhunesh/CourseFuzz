from __future__ import annotations

import hashlib
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
    return candidate.model_copy(update={"id": f"patch-{digest[:12]}", "payload_sha256": digest})


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
        before, survivors = self._measure(assignment, assignment.instructor_tests, deadline)
        if not survivors:
            return AnalysisResult(
                before=before,
                projected_after=before,
                survivors_before=(),
                hypothesis_verdicts=(),
                evidence=self._evidence(assignment, (), finding=False),
            )

        context = HypothesisContext.from_assignment(assignment)
        survivor_hints = tuple(
            SurvivorHint(id=item.id, misconception=item.misconception) for item in survivors
        )

        max_attempts = 2
        for attempt in range(max_attempts):
            proposed = self.hypotheses.propose(context, survivor_hints)
            verdicts, decisions = self._verify_hypotheses(assignment, survivors, proposed, deadline)
            verified = [verdict for verdict in verdicts if verdict.status == "verified"]
            if verified:
                break

            # If we failed and have another attempt, feed the failure reasons back
            if attempt < max_attempts - 1:
                feedback = [
                    f"Hypothesis {v.hypothesis.inputs} rejected: {v.reason}" for v in verdicts
                ]
                context = context.model_copy(update={"previous_feedback": tuple(feedback)})

        if not verified:
            return AnalysisResult(
                before=before,
                projected_after=before,
                survivors_before=tuple(item.id for item in survivors),
                hypothesis_verdicts=verdicts,
                evidence=self._evidence(assignment, verdicts, finding=False),
            )

        # Select maximum misconception coverage from inputs already proven by the independent
        # oracle. Tie-break toward a small, legible witness. Isolation startup now scales with
        # programs rather than programs × hypotheses or the size of the declared input domain.
        selected = min(
            verified,
            key=lambda verdict: (
                -len(verdict.killed_mutants),
                sum(abs(value) for value in verdict.hypothesis.inputs),
                max(abs(value) for value in verdict.hypothesis.inputs),
                verdict.hypothesis.inputs,
                verdict.hypothesis.id,
            ),
        )
        decision = decisions[selected.hypothesis.inputs]
        if decision.expected is None or selected.actual is None:
            raise ValueError("A verified hypothesis lost its executable oracle evidence")
        target = self._program_by_id(survivors, selected.killed_mutants[0])
        verified_test = TestCase(
            inputs=selected.hypothesis.inputs,
            expected=decision.expected,
            label=f"CourseFuzz regression: {target.misconception}",
            source=("gpt-5.6" if selected.hypothesis.provider == "gpt-5.6" else "deterministic"),
        )
        hardened_tests = (*assignment.instructor_tests, verified_test)
        projected_after, _ = self._measure(assignment, hardened_tests, deadline)
        candidate = self._build_patch(
            verified_test,
            selected.killed_mutants,
            assignment.entrypoint,
            selected.actual,
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
            evidence=self._evidence(assignment, verdicts, finding=True, decision=decision),
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

    def _verify_hypotheses(
        self,
        assignment: AssignmentSpec,
        survivors: tuple[ProgramVariant, ...],
        hypotheses: tuple[AttackHypothesis, ...],
        deadline: float,
    ) -> tuple[tuple[HypothesisVerdict, ...], dict[tuple[int, ...], OracleDecision]]:
        """Verify every hypothesis with two batched execution waves.

        Accepted controls run once over all unique inputs, then surviving misconception programs
        run once over all oracle-resolved inputs. Isolation startup therefore scales with programs,
        not ``programs × hypotheses``.
        """

        invalid: dict[str, str] = {}
        valid_inputs: list[tuple[int, ...]] = []
        for hypothesis in hypotheses:
            if len(hypothesis.inputs) != len(assignment.input_names):
                invalid[hypothesis.id] = "Input arity does not match the assignment signature."
            elif any(
                value < assignment.domain_min or value > assignment.domain_max
                for value in hypothesis.inputs
            ):
                invalid[hypothesis.id] = "Input is outside the declared bounded domain."
            elif hypothesis.inputs not in valid_inputs:
                valid_inputs.append(hypothesis.inputs)

        probe_tests = tuple(
            TestCase(
                inputs=inputs,
                expected=None,
                label="oracle probe",
                source="deterministic",
            )
            for inputs in valid_inputs
        )
        control_executions = self._run_suite_batch(
            assignment.accepted_solutions,
            assignment.entrypoint,
            probe_tests,
            deadline,
        )
        control_outputs = self._outputs_by_program(
            assignment.accepted_solutions, control_executions
        )
        decisions = {
            inputs: self.oracle.decide(
                assignment,
                inputs,
                lambda program, _inputs=inputs: control_outputs.get(program.id, {}).get(_inputs),
            )
            for inputs in valid_inputs
        }
        resolved_tests = tuple(
            TestCase(
                inputs=inputs,
                expected=decision.expected,
                label="candidate",
                source="deterministic",
            )
            for inputs, decision in decisions.items()
            if decision.resolved and decision.expected is not None
        )
        survivor_executions = self._run_suite_batch(
            survivors,
            assignment.entrypoint,
            resolved_tests,
            deadline,
        )
        survivor_outputs = self._outputs_by_program(survivors, survivor_executions)

        verdicts: list[HypothesisVerdict] = []
        for hypothesis in hypotheses:
            if hypothesis.id in invalid:
                verdicts.append(
                    HypothesisVerdict(
                        hypothesis=hypothesis,
                        status="rejected",
                        reason=invalid[hypothesis.id],
                    )
                )
                continue
            decision = decisions[hypothesis.inputs]
            if not decision.resolved or decision.expected is None:
                verdicts.append(
                    HypothesisVerdict(
                        hypothesis=hypothesis,
                        status="rejected",
                        reason=("Independent accepted solutions disagreed; the oracle abstained."),
                    )
                )
                continue
            killed: list[str] = []
            observed_actual: JsonAtom | None = None
            for survivor in survivors:
                actual = survivor_outputs.get(survivor.id, {}).get(hypothesis.inputs)
                if actual is None or actual == decision.expected:
                    continue
                killed.append(survivor.id)
                if observed_actual is None:
                    observed_actual = actual
            if not killed:
                verdicts.append(
                    HypothesisVerdict(
                        hypothesis=hypothesis,
                        status="rejected",
                        reason="Execution found no behavioral divergence.",
                        expected=decision.expected,
                    )
                )
                continue
            verdicts.append(
                HypothesisVerdict(
                    hypothesis=hypothesis,
                    status="verified",
                    reason="Execution reproduced a real output disagreement.",
                    expected=decision.expected,
                    actual=observed_actual,
                    killed_mutants=tuple(killed),
                )
            )
        return tuple(verdicts), decisions

    @staticmethod
    def _outputs_by_program(
        programs: tuple[ProgramVariant, ...],
        executions: list[SuiteExecution],
    ) -> dict[str, dict[tuple[int, ...], JsonAtom | None]]:
        outputs: dict[str, dict[tuple[int, ...], JsonAtom | None]] = {}
        for program, execution in zip(programs, executions, strict=True):
            row: dict[tuple[int, ...], JsonAtom | None] = {}
            for entry in execution.outputs:
                actual = entry.get("actual")
                row[tuple(entry.get("inputs", ()))] = (
                    actual if isinstance(actual, (str, int, float, bool)) else None
                )
            outputs[program.id] = row
        return outputs

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
                f"on the verified input {test.inputs}."
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
        decision: OracleDecision | None = None,
    ) -> dict:
        return {
            "oracle": f"{len(assignment.accepted_solutions)} independently checked controls",
            "truth_source": "compiled restricted Python executions",
            "hypothesis_providers": sorted({item.hypothesis.provider for item in verdicts}),
            "search_domain": [assignment.domain_min, assignment.domain_max],
            "domain_cases": (assignment.domain_max - assignment.domain_min + 1)
            ** len(assignment.input_names),
            "selection_strategy": "execution-verified maximum hypothesis coverage",
            "gpt_decides_correctness": False,
            "finding": finding,
            "oracle_evidence": AssessmentEngine._oracle_evidence(assignment, verdicts, decision),
        }

    @staticmethod
    def _oracle_evidence(
        assignment: AssignmentSpec,
        verdicts: tuple[HypothesisVerdict, ...] | list[HypothesisVerdict],
        decision: OracleDecision | None,
    ) -> dict:
        """Make the truth source legible: how the expected output was established, or why not.

        For a finding, this records the resolved oracle's provenance, agreeing sources, and quorum.
        For an abstention it records the distinct reasons the oracle refused to establish truth —
        the honest half of the loop, so "no finding" is never a silent black box.
        """
        controls = len(assignment.accepted_solutions)
        if decision is not None and decision.resolved:
            return {
                "decision": "resolved",
                "provenance": decision.provenance,
                "sources": list(decision.evidence_sources),
                "quorum": decision.quorum,
                "controls": controls,
            }
        abstention_reasons = sorted(
            {
                verdict.reason
                for verdict in verdicts
                if verdict.status == "rejected" and "abstain" in verdict.reason.lower()
            }
        )
        return {
            "decision": "abstained" if abstention_reasons else "no_counterexample",
            "abstention_reasons": abstention_reasons,
            "controls": controls,
        }
