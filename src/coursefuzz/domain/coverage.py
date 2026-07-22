from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class MutantEquivalenceCluster:
    """Group of mutant programs with identical test-failure signatures."""

    signature: tuple[bool, ...]
    mutant_ids: tuple[str, ...]


@dataclass(frozen=True)
class SubsumptionPair:
    """Represents a subsumption relationship where parent_test subsumes child_test."""

    parent_test_label: str
    child_test_label: str


@dataclass(frozen=True)
class DifferentialMatrixResult:
    """Analysis result of the 2D Test-Mutant execution matrix."""

    matrix: dict[str, dict[str, bool]]  # test_label -> {mutant_id -> passed}
    equivalence_clusters: tuple[MutantEquivalenceCluster, ...]
    subsumed_tests: tuple[SubsumptionPair, ...]


def compute_differential_matrix(
    test_labels: Sequence[str],
    mutant_ids: Sequence[str],
    results_grid: dict[str, dict[str, bool]],  # test_label -> {mutant_id -> passed}
) -> DifferentialMatrixResult:
    """Compute 2D mutant coverage matrix, mutant clusters, and test subsumption pairs."""
    
    # 1. Equivalence Clusters (Group mutants by failure vector across all tests)
    mutant_signatures: dict[str, list[str]] = {}
    for mut_id in mutant_ids:
        # Build boolean tuple where True means passed, False means killed
        sig = tuple(results_grid.get(label, {}).get(mut_id, True) for label in test_labels)
        key = str(sig)
        if key not in mutant_signatures:
            mutant_signatures[key] = []
        mutant_signatures[key].append(mut_id)

    clusters = tuple(
        MutantEquivalenceCluster(
            signature=eval(key),
            mutant_ids=tuple(mut_ids),
        )
        for key, mut_ids in mutant_signatures.items()
    )

    # 2. Test Subsumption (Test A subsumes Test B if Test A kills all mutants Test B kills AND more)
    subsumption_pairs: list[SubsumptionPair] = []
    
    # Precalculate killed mutant sets for each test
    killed_sets: dict[str, set[str]] = {
        label: {mut_id for mut_id in mutant_ids if not results_grid.get(label, {}).get(mut_id, True)}
        for label in test_labels
    }

    for i, t1 in enumerate(test_labels):
        for j, t2 in enumerate(test_labels):
            if i != j:
                k1, k2 = killed_sets[t1], killed_sets[t2]
                if k2 and k1.issuperset(k2) and k1 != k2:
                    subsumption_pairs.append(
                        SubsumptionPair(parent_test_label=t1, child_test_label=t2)
                    )

    return DifferentialMatrixResult(
        matrix=results_grid,
        equivalence_clusters=clusters,
        subsumed_tests=tuple(subsumption_pairs),
    )
