from __future__ import annotations

from coursefuzz.domain.ast_analyzer import analyze_source_ast
from coursefuzz.domain.sandbox import run_in_isolated_sandbox
from coursefuzz.domain.coverage import compute_differential_matrix


def test_ast_analyzer_extracts_boundary_constants_and_operators() -> None:
    code = (
        "def absolute_value(n):\n"
        "    if n < 0:\n"
        "        return -n\n"
        "    elif n == 100:\n"
        "        return 100\n"
        "    return n\n"
    )
    invariants = analyze_source_ast(code)
    assert 0 in invariants.boundary_constants
    assert 100 in invariants.boundary_constants
    assert "Lt" in invariants.comparison_operators
    assert "Eq" in invariants.comparison_operators
    assert invariants.branch_count == 2
    assert invariants.has_loops is False


def test_isolated_sandbox_executes_safely_and_captures_output() -> None:
    code = "print('hello_sandbox')\n"
    res = run_in_isolated_sandbox(code, timeout_seconds=1.0)
    assert res.returncode == 0
    assert "hello_sandbox" in res.stdout
    assert res.timed_out is False


def test_isolated_sandbox_handles_timeout() -> None:
    code = "import time\ntime.sleep(5)\n"
    res = run_in_isolated_sandbox(code, timeout_seconds=0.5)
    assert res.timed_out is True


def test_compute_differential_matrix_and_subsumption() -> None:
    test_labels = ["test_1", "test_2"]
    mutant_ids = ["mut_A", "mut_B"]
    
    # test_1 kills mut_A and mut_B; test_2 kills mut_A only
    grid = {
        "test_1": {"mut_A": False, "mut_B": False},
        "test_2": {"mut_A": False, "mut_B": True},
    }
    
    diff = compute_differential_matrix(test_labels, mutant_ids, grid)
    assert len(diff.equivalence_clusters) == 2
    # test_1 subsumes test_2 because test_1 kills a strict superset of test_2
    assert len(diff.subsumed_tests) == 1
    assert diff.subsumed_tests[0].parent_test_label == "test_1"
    assert diff.subsumed_tests[0].child_test_label == "test_2"
