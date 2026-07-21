from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from itertools import permutations, product

from pydantic import BaseModel, Field

from coursefuzz.domain.models import AssignmentSpec, AttackHypothesis


class HypothesisBatch(BaseModel):
    hypotheses: list[ModelHypothesis] = Field(min_length=1, max_length=8)


class ModelHypothesis(BaseModel):
    inputs: tuple[int, ...]
    rationale: str
    misconception: str


class ExistingTestView(BaseModel):
    inputs: tuple[int, ...]
    label: str


class SurvivorHint(BaseModel):
    id: str
    misconception: str


class HypothesisContext(BaseModel):
    """Sanitized, source-free input available to a hypothesis provider."""

    title: str
    summary: str
    input_names: tuple[str, ...]
    domain_min: int
    domain_max: int
    existing_tests: tuple[ExistingTestView, ...]

    @classmethod
    def from_assignment(cls, assignment: AssignmentSpec) -> HypothesisContext:
        return cls(
            title=assignment.title,
            summary=assignment.summary,
            input_names=assignment.input_names,
            domain_min=assignment.domain_min,
            domain_max=assignment.domain_max,
            existing_tests=tuple(
                ExistingTestView(inputs=test.inputs, label=test.label)
                for test in assignment.instructor_tests
            ),
        )


class HypothesisProvider(ABC):
    mode: str

    @abstractmethod
    def propose(
        self,
        context: HypothesisContext,
        survivors: tuple[SurvivorHint, ...],
    ) -> tuple[AttackHypothesis, ...]:
        raise NotImplementedError


class DeterministicHypothesisProvider(HypothesisProvider):
    mode = "deterministic-fallback"

    def propose(
        self,
        context: HypothesisContext,
        survivors: tuple[SurvivorHint, ...],
    ) -> tuple[AttackHypothesis, ...]:
        del survivors
        existing = {test.inputs for test in context.existing_tests}
        candidates: list[tuple[tuple[int, ...], str, str]] = []

        ordered_tests = sorted(
            context.existing_tests,
            key=lambda item: (len(set(item.inputs)), item.label),
        )
        for test in ordered_tests:
            for permuted in sorted(set(permutations(test.inputs))):
                if permuted != test.inputs:
                    candidates.append(
                        (
                            permuted,
                            f"Permute the instructor case labelled '{test.label}'.",
                            "input-order blind spot",
                        )
                    )

        boundaries = sorted(
            {
                context.domain_min,
                context.domain_max,
                0,
                max(context.domain_min, min(context.domain_max, 1)),
            }
            & set(range(context.domain_min, context.domain_max + 1))
        )
        for values in product(boundaries, repeat=len(context.input_names)):
            candidates.append(
                (
                    tuple(values),
                    "Combine declared domain boundaries across every input position.",
                    "boundary interaction",
                )
            )

        selected: list[tuple[tuple[int, ...], str, str]] = []
        seen = set(existing)
        for item in candidates:
            if item[0] in seen:
                continue
            seen.add(item[0])
            selected.append(item)
            if len(selected) == 8:
                break
        return tuple(
            AttackHypothesis(
                id=f"hypothesis-{index + 1}",
                inputs=inputs,
                rationale=rationale,
                misconception=misconception,
                provider="deterministic-fallback",
            )
            for index, (inputs, rationale, misconception) in enumerate(selected)
        )


class OpenAIHypothesisProvider(HypothesisProvider):
    mode = "live-gpt-5.6"

    def __init__(self, model: str | None = None) -> None:
        from openai import OpenAI

        self.client = OpenAI(timeout=20.0, max_retries=1)
        self.model = model or os.getenv("COURSEFUZZ_MODEL", "gpt-5.6-sol")

    def propose(
        self,
        context: HypothesisContext,
        survivors: tuple[SurvivorHint, ...],
    ) -> tuple[AttackHypothesis, ...]:
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
                    "assignment": context.model_dump(mode="json"),
                    "surviving_misconceptions": [
                        item.model_dump(mode="json") for item in survivors
                    ],
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
        self,
        context: HypothesisContext,
        survivors: tuple[SurvivorHint, ...],
    ) -> tuple[AttackHypothesis, ...]:
        try:
            return self.primary.propose(context, survivors)
        except Exception:
            return self.fallback.propose(context, survivors)


def build_hypothesis_provider() -> HypothesisProvider:
    if not os.getenv("OPENAI_API_KEY"):
        return DeterministicHypothesisProvider()
    try:
        return ResilientHypothesisProvider(
            OpenAIHypothesisProvider(), DeterministicHypothesisProvider()
        )
    except Exception:
        return DeterministicHypothesisProvider()
