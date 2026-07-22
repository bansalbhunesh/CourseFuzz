from __future__ import annotations

import json
import os
import queue
import threading
from abc import ABC, abstractmethod
from itertools import permutations, product

from pydantic import BaseModel, Field

from coursefuzz.domain.ast_analyzer import analyze_source_ast
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
    ast_constants: tuple[int, ...] = ()
    ast_operators: tuple[str, ...] = ()
    previous_feedback: tuple[str, ...] = ()

    @classmethod
    def from_assignment(cls, assignment: AssignmentSpec) -> HypothesisContext:
        ast_info = analyze_source_ast(assignment.reference.source)
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
            ast_constants=ast_info.boundary_constants,
            ast_operators=ast_info.comparison_operators,
            previous_feedback=(),
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
                    # Equality-pattern tests often hide a second bug behind a magnitude guard.
                    # Move the one distinct position to the smallest positive in-domain value;
                    # this keeps the equality pattern while probing the missing order/magnitude
                    # interaction (for example 3,2,2 -> 1,2,2).
                    anchor = max(context.domain_min, min(context.domain_max, 1))
                    for index, value in enumerate(permuted):
                        if permuted.count(value) != 1 or value == anchor:
                            continue
                        shrunk = list(permuted)
                        shrunk[index] = anchor
                        candidates.append(
                            (
                                tuple(shrunk),
                                "Preserve the equality pattern while shrinking its distinct input.",
                                "order and magnitude interaction",
                            )
                        )

        boundaries = sorted(
            {
                context.domain_min,
                min(context.domain_max, context.domain_min + 1),
                context.domain_max,
                max(context.domain_min, context.domain_max - 1),
                0,
                max(context.domain_min, min(context.domain_max, 1)),
            }
            & set(range(context.domain_min, context.domain_max + 1))
        )
        boundary_cases = sorted(
            product(boundaries, repeat=len(context.input_names)),
            key=lambda values: (
                sum(abs(value) for value in values),
                max(abs(value) for value in values),
                values,
            ),
        )
        for values in boundary_cases:
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

    def __init__(
        self,
        model: str | None = None,
        *,
        timeout_seconds: float = 12.0,
    ) -> None:
        from openai import OpenAI

        # The engine owns a 30-second end-to-end deadline. Keep the optional model call well
        # inside that budget so a timeout can still fall back to deterministic hypotheses and
        # leave enough time for the execution oracle. Retrying here would consume the oracle's
        # budget; a fresh user run is the explicit retry boundary.
        self.client = OpenAI(timeout=timeout_seconds, max_retries=0)
        self.model = model or os.getenv("COURSEFUZZ_MODEL", "gpt-5.6-sol")

    def propose(
        self,
        context: HypothesisContext,
        survivors: tuple[SurvivorHint, ...],
    ) -> tuple[AttackHypothesis, ...]:
        response = self.client.beta.chat.completions.parse(
            model=self.model,
            reasoning={"effort": "low"},
            response_format=HypothesisBatch,
            messages=[
                {
                    "role": "system",
                    "content": "Generate bounded test-input hypotheses for an introductory programming "
                               "assignment. Inputs are hypotheses only: never claim correctness or invent "
                               "expected outputs. The execution oracle will reject most candidates. Stay "
                               "inside the declared integer domain and return at most eight diverse cases."
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "assignment": context.model_dump(mode="json"),
                            "surviving_misconceptions": [
                                item.model_dump(mode="json") for item in survivors
                            ],
                            "previous_feedback": context.previous_feedback,
                        },
                        sort_keys=True,
                    )
                }
            ],
            max_tokens=1400,
        )
        parsed = response.choices[0].message.parsed
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

    def __init__(
        self,
        primary: HypothesisProvider,
        fallback: HypothesisProvider,
        *,
        primary_wall_seconds: float = 12.0,
        max_concurrent_primary_calls: int = 4,
    ) -> None:
        if primary_wall_seconds <= 0:
            raise ValueError("primary_wall_seconds must be positive")
        if max_concurrent_primary_calls <= 0:
            raise ValueError("max_concurrent_primary_calls must be positive")
        self.primary = primary
        self.fallback = fallback
        self.primary_wall_seconds = primary_wall_seconds
        # A timed-out network call may need a short period to unwind. Bound those daemon calls so
        # repeated timeouts cannot create an unbounded thread pile-up under load.
        self._primary_slots = threading.BoundedSemaphore(max_concurrent_primary_calls)

    def propose(
        self,
        context: HypothesisContext,
        survivors: tuple[SurvivorHint, ...],
    ) -> tuple[AttackHypothesis, ...]:
        if not self._primary_slots.acquire(blocking=False):
            return self.fallback.propose(context, survivors)

        result: queue.Queue[tuple[AttackHypothesis, ...] | Exception] = queue.Queue(maxsize=1)

        def call_primary() -> None:
            try:
                result.put(self.primary.propose(context, survivors))
            except Exception as exc:
                result.put(exc)
            finally:
                self._primary_slots.release()

        threading.Thread(
            target=call_primary,
            name="coursefuzz-hypothesis-provider",
            daemon=True,
        ).start()
        try:
            outcome = result.get(timeout=self.primary_wall_seconds)
        except queue.Empty:
            return self.fallback.propose(context, survivors)
        if isinstance(outcome, Exception):
            return self.fallback.propose(context, survivors)
        # Live hypotheses provide semantic targeting; deterministic candidates guarantee stable
        # boundary and permutation coverage. Reserve half the fixed eight-item budget for each,
        # deduplicate by input, and reissue stable IDs for the combined batch.
        fallback = self.fallback.propose(context, survivors)
        combined: list[AttackHypothesis] = []
        seen: set[tuple[int, ...]] = set()
        for item in (*outcome[:4], *fallback):
            if item.inputs in seen:
                continue
            seen.add(item.inputs)
            combined.append(
                item.model_copy(update={"id": f"hypothesis-{len(combined) + 1}"})
            )
            if len(combined) == 8:
                break
        return tuple(combined)


def build_hypothesis_provider() -> HypothesisProvider:
    if not os.getenv("OPENAI_API_KEY"):
        return DeterministicHypothesisProvider()
    try:
        return ResilientHypothesisProvider(
            OpenAIHypothesisProvider(), DeterministicHypothesisProvider()
        )
    except Exception:
        return DeterministicHypothesisProvider()
