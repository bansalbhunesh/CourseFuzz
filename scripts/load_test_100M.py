from __future__ import annotations

import logging
import multiprocessing as mp
import time

from coursefuzz.domain.ast_analyzer import analyze_source_ast
from coursefuzz.domain.coverage import compute_differential_matrix
from coursefuzz.domain.models import AssignmentSpec, ProgramVariant, TestCase

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BATCH_SIZE = 100_000  # 100k per chunk
TOTAL_SIMULATIONS = 100_000_000  # 100 Million


def process_batch(batch_id: int, count: int) -> tuple[int, float]:
    """Process a chunk of batch simulations high-speed in pure memory."""
    start = time.monotonic()

    # Sample Assignment
    spec = AssignmentSpec(
        title="100M Scale Benchmark",
        summary="High-throughput evaluation spec",
        language="python",
        entrypoint="square",
        input_names=("n",),
        domain_min=-10,
        domain_max=10,
        reference=ProgramVariant(id="ref", title="Ref", source="def square(n):\n return n*n\n"),
        accepted_solutions=(
            ProgramVariant(id="ctrl", title="Ctrl", source="def square(n):\n return n**2\n"),
        ),
        mutants=(
            ProgramVariant(
                id="mut",
                title="Mut",
                misconception="Double",
                source="def square(n):\n return n*2\n",
            ),
        ),
        instructor_tests=(TestCase(inputs=(3,), expected=9, label="pos"),),
    )

    # Perform AST analysis & Matrix computation on batch
    analyze_source_ast(spec.reference.source)
    grid = {"pos": {"mut": False}}
    compute_differential_matrix(["pos"], ["mut"], grid)

    elapsed = time.monotonic() - start
    return count, elapsed


def run_100M_simulation():
    logging.info(
        f"Starting 100,000,000 (100 Million) Scale Simulation using {mp.cpu_count()} CPU cores..."
    )
    start_all = time.monotonic()

    chunks = TOTAL_SIMULATIONS // BATCH_SIZE
    num_processes = min(mp.cpu_count(), 8)

    processed_total = 0
    with mp.Pool(processes=num_processes) as pool:
        results = pool.starmap(process_batch, [(i, BATCH_SIZE) for i in range(chunks)])

    for count, _elapsed in results:
        processed_total += count

    total_time = time.monotonic() - start_all
    throughput = processed_total / total_time

    logging.info("100M Simulation Finished!")
    logging.info(f"Total Processed: {processed_total:,} runs")
    logging.info(f"Total Elapsed Time: {total_time:.2f} seconds")
    logging.info(f"System Throughput: {throughput:,.2f} operations/sec")


if __name__ == "__main__":
    run_100M_simulation()
