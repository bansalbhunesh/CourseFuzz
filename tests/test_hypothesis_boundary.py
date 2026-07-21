import json

from coursefuzz.adapters.hypotheses import HypothesisContext, SurvivorHint
from coursefuzz.data.demo import TRIANGLE_ASSIGNMENT


def test_hypothesis_context_excludes_code_oracle_answers_and_frozen_labels() -> None:
    context = HypothesisContext.from_assignment(TRIANGLE_ASSIGNMENT)
    survivors = tuple(
        SurvivorHint(id=item.id, misconception=item.misconception)
        for item in TRIANGLE_ASSIGNMENT.mutants
    )
    serialized = json.dumps(
        {
            "assignment": context.model_dump(mode="json"),
            "survivors": [item.model_dump(mode="json") for item in survivors],
        },
        sort_keys=True,
    )

    assert "expected" not in serialized
    assert "source" not in serialized
    assert TRIANGLE_ASSIGNMENT.reference.source not in serialized
    assert "frozen_expectations" not in serialized
    assert all(item["inputs"] for item in context.model_dump(mode="json")["existing_tests"])
