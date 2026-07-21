from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from coursefuzz.domain.models import AssignmentSpec, AttackHypothesis, ProgramVariant


class HypothesisBatch(BaseModel):
    hypotheses: list[ModelHypothesis] = Field(min_length=1, max_length=8)


class ModelHypothesis(BaseModel):
    inputs: tuple[int, ...]
    rationale: str
    misconception: str


class HypothesisProvider(ABC):
    mode: str

    @abstractmethod
    def propose(
        self, assignment: AssignmentSpec, survivors: tuple[ProgramVariant, ...]
    ) -> tuple[AttackHypothesis, ...]:
        raise NotImplementedError


class DeterministicHypothesisProvider(HypothesisProvider):
    mode = "deterministic-fallback"

    def propose(
        self, assignment: AssignmentSpec, survivors: tuple[ProgramVariant, ...]
    ) -> tuple[AttackHypothesis, ...]:
        del assignment, survivors
        examples = (
            ((5, 5, 8), "Repeat the first side pair.", "pair-order coverage"),
            (
                (4, 5, 5),
                "Move the repeated pair to the last two positions.",
                "permutation blind spot",
            ),
            ((5, 4, 5), "Move the repeated pair to the outer positions.", "permutation blind spot"),
            (
                (2, 3, 5),
                "Probe the equality boundary of the triangle inequality.",
                "boundary condition",
            ),
        )
        return tuple(
            AttackHypothesis(
                id=f"hypothesis-{index + 1}",
                inputs=inputs,
                rationale=rationale,
                misconception=misconception,
                provider="deterministic-fallback",
            )
            for index, (inputs, rationale, misconception) in enumerate(examples)
        )


class OpenAIHypothesisProvider(HypothesisProvider):
    mode = "live-gpt-5.6"

    def __init__(self, model: str | None = None) -> None:
        from openai import OpenAI

        self.client = OpenAI(timeout=20.0, max_retries=1)
        self.model = model or os.getenv("COURSEFUZZ_MODEL", "gpt-5.6-sol")

    def propose(
        self, assignment: AssignmentSpec, survivors: tuple[ProgramVariant, ...]
    ) -> tuple[AttackHypothesis, ...]:
        survivor_context = [
            {"id": item.id, "misconception": item.misconception} for item in survivors
        ]
        response = self.client.responses.parse(
            model=self.model,
            reasoning={"effort": "medium"},
            text_format=HypothesisBatch,
            max_output_tokens=1400,
            store=False,
            safety_identifier="coursefuzz-demo",
            instructions=(
                "Generate bounded test-input hypotheses for an introductory programming "
                "assignment. Inputs are hypotheses only: never claim correctness or invent "
                "expected outputs. The execution oracle will reject most candidates. Stay "
                "inside the declared integer domain and return at most eight diverse cases."
            ),
            input=json.dumps(
                {
                    "assignment": {
                        "title": assignment.title,
                        "summary": assignment.summary,
                        "input_names": assignment.input_names,
                        "domain": [assignment.domain_min, assignment.domain_max],
                        "existing_tests": [
                            test.model_dump(mode="json") for test in assignment.instructor_tests
                        ],
                    },
                    "surviving_misconceptions": survivor_context,
                },
                sort_keys=True,
            ),
        )
        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError("GPT-5.6 returned no structured hypothesis batch")
        return tuple(
            AttackHypothesis(
                id=f"hypothesis-{index + 1}",
                inputs=item.inputs,
                rationale=item.rationale,
                misconception=item.misconception,
                provider="gpt-5.6",
            )
            for index, item in enumerate(parsed.hypotheses)
        )


class ResilientHypothesisProvider(HypothesisProvider):
    mode = "live-gpt-5.6"

    def __init__(self, primary: HypothesisProvider, fallback: HypothesisProvider) -> None:
        self.primary = primary
        self.fallback = fallback

    def propose(
        self, assignment: AssignmentSpec, survivors: tuple[ProgramVariant, ...]
    ) -> tuple[AttackHypothesis, ...]:
        try:
            return self.primary.propose(assignment, survivors)
        except Exception:
            return self.fallback.propose(assignment, survivors)


def build_hypothesis_provider() -> HypothesisProvider:
    if not os.getenv("OPENAI_API_KEY"):
        return DeterministicHypothesisProvider()
    try:
        return ResilientHypothesisProvider(
            OpenAIHypothesisProvider(), DeterministicHypothesisProvider()
        )
    except Exception:
        return DeterministicHypothesisProvider()
