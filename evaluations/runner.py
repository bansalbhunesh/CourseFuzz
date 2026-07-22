from __future__ import annotations

import hashlib
import itertools
import json
import math
import random
import statistics
import time
from pathlib import Path

from coursefuzz.adapters.hypotheses import (
    DeterministicHypothesisProvider,
    HypothesisContext,
    HypothesisProvider,
    SurvivorHint,
)
from coursefuzz.adapters.runner import validate_source
from coursefuzz.adapters.sandbox import SubprocessPythonSandbox
from coursefuzz.domain.engine import AssessmentEngine
from coursefuzz.domain.models import AssignmentSpec, AttackHypothesis
from evaluations.cases import frozen_cases

ROOT = Path(__file__).resolve().parents[1]
EXPECTATIONS_PATH = ROOT / "evaluations" / "frozen_expectations.json"


class FrozenRandomProvider(HypothesisProvider):
    """Equal-budget input baseline with a stable seed and no semantic heuristics."""

    mode = "deterministic-fallback"

    def propose(
        self,
        context: HypothesisContext,
        survivors: tuple[SurvivorHint, ...],
    ) -> tuple[AttackHypothesis, ...]:
        del survivors
        seed_payload = json.dumps(context.model_dump(mode="json"), sort_keys=True).encode()
        seed = int.from_bytes(hashlib.sha256(seed_payload).digest()[:8], "big")
        generator = random.Random(seed)
        existing = {test.inputs for test in context.existing_tests}
        candidates = [
            tuple(values)
            for values in itertools.product(
                range(context.domain_min, context.domain_max + 1),
                repeat=len(context.input_names),
            )
            if tuple(values) not in existing
        ]
        generator.shuffle(candidates)
        return tuple(
            AttackHypothesis(
                id=f"random-{index + 1}",
                inputs=inputs,
                rationale="Frozen random input baseline.",
                misconception="random baseline",
                provider="deterministic-fallback",
            )
            for index, inputs in enumerate(candidates[:8])
        )


def corpus_sha256(cases: tuple[AssignmentSpec, ...]) -> str:
    payload = [case.model_dump(mode="json") for case in cases]
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(serialized).hexdigest()


def _percent(numerator: int, denominator: int) -> float:
    return round((numerator / denominator) * 100, 1) if denominator else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def _validate_case(case: AssignmentSpec) -> None:
    if len(case.accepted_solutions) < 2:
        raise ValueError(f"{case.id}: benchmark cases require two accepted controls")
    if len({program.source for program in case.accepted_solutions}) != len(case.accepted_solutions):
        raise ValueError(f"{case.id}: accepted controls must be independently authored")
    for program in (*case.accepted_solutions, *case.mutants):
        validate_source(program.source, case.entrypoint)


def run_inference() -> dict:
    """Run both systems without loading frozen acceptance thresholds."""

    cases = frozen_cases()
    sandbox = SubprocessPythonSandbox()
    coursefuzz_engine = AssessmentEngine(
        sandbox,
        DeterministicHypothesisProvider(),
        max_analysis_seconds=30,
    )
    random_engine = AssessmentEngine(
        sandbox,
        FrozenRandomProvider(),
        max_analysis_seconds=30,
    )

    case_results: list[dict] = []
    total_mutants = 0
    initial_killed = 0
    coursefuzz_killed = 0
    random_killed = 0
    accepted_programs = 0
    coursefuzz_false_kills = 0
    findings = 0
    latencies: list[float] = []

    for case in cases:
        _validate_case(case)
        started = time.perf_counter()
        analysis = coursefuzz_engine.analyze(case)
        latency = time.perf_counter() - started
        random_analysis = random_engine.analyze(case)

        total_mutants += analysis.before.total_mutants
        initial_killed += analysis.before.killed_mutants
        coursefuzz_killed += analysis.projected_after.killed_mutants
        random_killed += random_analysis.projected_after.killed_mutants
        accepted_programs += len(case.accepted_solutions)
        coursefuzz_false_kills += round(
            len(case.accepted_solutions)
            * (100 - analysis.projected_after.accepted_solution_pass_rate)
            / 100
        )
        findings += int(analysis.candidate is not None)
        latencies.append(latency)
        case_results.append(
            {
                "assignment_id": case.id,
                "mutants": analysis.before.total_mutants,
                "instructor_killed": analysis.before.killed_mutants,
                "coursefuzz_killed": analysis.projected_after.killed_mutants,
                "random_8_killed": random_analysis.projected_after.killed_mutants,
                "coursefuzz_finding": analysis.candidate is not None,
                "random_8_finding": random_analysis.candidate is not None,
                "selected_input": (
                    list(analysis.candidate.test.inputs) if analysis.candidate else None
                ),
                "accepted_solution_pass_rate": (
                    analysis.projected_after.accepted_solution_pass_rate
                ),
                "latency_seconds": round(latency, 3),
            }
        )

    aggregate = {
        "assignments": len(cases),
        "wrong_programs": total_mutants,
        "accepted_controls": accepted_programs,
        "instructor_mutation_score": _percent(initial_killed, total_mutants),
        "coursefuzz_mutation_score": _percent(coursefuzz_killed, total_mutants),
        "random_8_mutation_score": _percent(random_killed, total_mutants),
        "coursefuzz_gain_points": round(
            _percent(coursefuzz_killed, total_mutants) - _percent(initial_killed, total_mutants),
            1,
        ),
        "coursefuzz_advantage_over_random_8_points": round(
            _percent(coursefuzz_killed, total_mutants) - _percent(random_killed, total_mutants),
            1,
        ),
        "false_kill_rate": _percent(coursefuzz_false_kills, accepted_programs),
        "finding_rate": _percent(findings, len(cases)),
        "abstention_rate": _percent(len(cases) - findings, len(cases)),
        "median_latency_seconds": round(statistics.median(latencies), 3),
        "p95_latency_seconds": _percentile(latencies, 0.95),
        "hypothesis_budget_per_assignment": 8,
    }
    return {
        "benchmark": "coursefuzz-synthetic-heldout-v1",
        "corpus_sha256": corpus_sha256(cases),
        "provider": "deterministic-fallback",
        "baseline": "frozen-random-8",
        "aggregate": aggregate,
        "cases": case_results,
    }


def verify_report(report: dict, expectations_path: Path = EXPECTATIONS_PATH) -> list[str]:
    """Load frozen thresholds only after inference and return any violations."""

    expectations = json.loads(expectations_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    if report["benchmark"] != expectations["benchmark"]:
        failures.append("benchmark version does not match the frozen expectation")
    if report["corpus_sha256"] != expectations["corpus_sha256"]:
        failures.append("corpus SHA-256 changed; freeze a new benchmark version intentionally")
    aggregate = report["aggregate"]
    for metric, minimum in expectations["minimums"].items():
        if aggregate[metric] < minimum:
            failures.append(f"{metric}={aggregate[metric]} is below frozen minimum {minimum}")
    for metric, maximum in expectations["maximums"].items():
        if aggregate[metric] > maximum:
            failures.append(f"{metric}={aggregate[metric]} exceeds frozen maximum {maximum}")
    observed_ids = {item["assignment_id"] for item in report["cases"]}
    expected_ids = set(expectations["assignment_ids"])
    if observed_ids != expected_ids:
        failures.append("assignment IDs do not match the frozen evaluation manifest")
    return failures


def write_report(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
