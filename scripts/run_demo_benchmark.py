from __future__ import annotations

import json

from coursefuzz.adapters.hypotheses import DeterministicHypothesisProvider
from coursefuzz.adapters.sandbox import SubprocessPythonSandbox
from coursefuzz.data.demo import TRIANGLE_ASSIGNMENT
from coursefuzz.domain.engine import AssessmentEngine


def main() -> None:
    result = AssessmentEngine(SubprocessPythonSandbox(), DeterministicHypothesisProvider()).analyze(
        TRIANGLE_ASSIGNMENT
    )
    print(
        json.dumps(
            {
                "assignment_id": TRIANGLE_ASSIGNMENT.id,
                "before": result.before.model_dump(mode="json"),
                "after": result.projected_after.model_dump(mode="json"),
                "minimal_counterexample": result.candidate.test.model_dump(mode="json"),
                "payload_sha256": result.candidate.payload_sha256,
                "survivors_before": result.survivors_before,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
