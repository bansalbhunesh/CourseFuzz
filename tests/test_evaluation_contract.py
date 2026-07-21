import json
from pathlib import Path

from evaluations.cases import frozen_cases
from evaluations.runner import corpus_sha256

ROOT = Path(__file__).resolve().parents[1]


def test_frozen_synthetic_corpus_matches_its_versioned_manifest() -> None:
    expectations = json.loads(
        (ROOT / "evaluations" / "frozen_expectations.json").read_text(encoding="utf-8")
    )
    cases = frozen_cases()

    assert len(cases) == 10
    assert sum(len(case.mutants) for case in cases) == 60
    assert sum(len(case.accepted_solutions) for case in cases) == 20
    assert corpus_sha256(cases) == expectations["corpus_sha256"]
    assert {case.id for case in cases} == set(expectations["assignment_ids"])
    assert all(len({item.source for item in case.accepted_solutions}) == 2 for case in cases)
