from __future__ import annotations

import concurrent.futures
import logging
import time
from dataclasses import dataclass

from coursefuzz.adapters.hypotheses import DeterministicHypothesisProvider
from coursefuzz.adapters.sandbox import LocalRestrictedRunner
from coursefuzz.domain.engine import AssessmentEngine
from coursefuzz.domain.models import AssignmentSpec, ProgramVariant, TestCase
from coursefuzz.domain.oracle import CompositeOracle

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


@dataclass
class LoadTestMetrics:
    total_runs: int
    successful_runs: int
    failed_runs: int
    total_time_seconds: float
    errors: list[str]


def create_sample_assignment(run_id: int) -> AssignmentSpec:
    return AssignmentSpec(
        title=f"Load Test Assignment {run_id}",
        summary="Compute non-negative magnitude of integer",
        language="python",
        entrypoint="absolute_value",
        input_names=("n",),
        domain_min=-10,
        domain_max=10,
        reference=ProgramVariant(
            id=f"ref-{run_id}",
            title="Reference",
            source="def absolute_value(n):\n    return n if n >= 0 else -n\n",
        ),
        accepted_solutions=(
            ProgramVariant(
                id=f"ctrl-{run_id}",
                title="Control",
                source="def absolute_value(n):\n    return abs(n)\n",
            ),
        ),
        mutants=(
            ProgramVariant(
                id=f"mut-{run_id}-1",
                title="Buggy Always Negate",
                misconception="Always negates",
                source="def absolute_value(n):\n    return -n\n",
            ),
            ProgramVariant(
                id=f"mut-{run_id}-2",
                title="Buggy Always Positive",
                misconception="Returns n unchanged",
                source="def absolute_value(n):\n    return n\n",
            ),
        ),
        instructor_tests=(
            TestCase(inputs=(-2,), expected=2, label="negative"),
            TestCase(inputs=(0,), expected=0, label="zero"),
        ),
    )


def simulate_user_run(run_id: int) -> tuple[bool, str | None]:
    try:
        assignment = create_sample_assignment(run_id)
        engine = AssessmentEngine(
            hypotheses=DeterministicHypothesisProvider(),
            oracle=CompositeOracle(),
            sandbox=LocalRestrictedRunner(),
            max_analysis_seconds=5.0,
        )
        result = engine.analyze(assignment)
        if result is None:
            return False, "Engine returned None result"
        return True, None
    except Exception as exc:
        return False, f"Run {run_id} failed: {type(exc).__name__}: {exc}"


def run_1000_user_load_test(
    concurrent_workers: int = 50, total_simulations: int = 1000
) -> LoadTestMetrics:
    logging.info(
        f"Starting 1,000 user load test simulation ({concurrent_workers} concurrent workers)..."
    )
    start_time = time.monotonic()

    successful = 0
    failed = 0
    errors: list[str] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrent_workers) as executor:
        futures = {executor.submit(simulate_user_run, i): i for i in range(total_simulations)}

        for future in concurrent.futures.as_completed(futures):
            run_id = futures[future]
            try:
                success, error_msg = future.result()
                if success:
                    successful += 1
                else:
                    failed += 1
                    if error_msg:
                        errors.append(error_msg)
            except Exception as exc:
                failed += 1
                errors.append(f"Unexpected executor exception on run {run_id}: {exc}")

            if (successful + failed) % 200 == 0:
                logging.info(f"Progress: {successful + failed}/{total_simulations} completed...")

    elapsed = time.monotonic() - start_time
    logging.info(f"Load test finished in {elapsed:.2f} seconds.")
    logging.info(
        f"Successful: {successful}/{total_simulations} | Failed: {failed}/{total_simulations}"
    )

    return LoadTestMetrics(
        total_runs=total_simulations,
        successful_runs=successful,
        failed_runs=failed,
        total_time_seconds=elapsed,
        errors=errors,
    )


if __name__ == "__main__":
    metrics = run_1000_user_load_test()
    if metrics.errors:
        print("\n--- SURFACED ERRORS & BOTTLENECKS ---")
        for err in metrics.errors[:10]:
            print(f"- {err}")
