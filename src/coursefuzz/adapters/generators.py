"""Budgeted, composable candidate generation (roadmap Milestone 4).

A single provider proposes inputs blind; this module lets several generators contribute under one
scheduler that deduplicates globally, records which generator produced each candidate, and holds
one shared candidate budget. It is a drop-in :class:`HypothesisProvider`, so the engine is
unchanged and the frozen benchmark is untouched unless a scheduler is wired in deliberately.

The two default generators reproduce the deterministic provider exactly — boundary combinations
plus equality-preserving permutation probes — so each contribution is attributable and ablatable.
The engine executes every candidate against independent controls and surviving misconception
programs in bounded batches; generators never decide correctness.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import permutations, product

from coursefuzz.adapters.hypotheses import HypothesisContext, HypothesisProvider, SurvivorHint
from coursefuzz.domain.models import AttackHypothesis


@dataclass(frozen=True)
class GeneratedCandidate:
    inputs: tuple[int, ...]
    rationale: str
    misconception: str
    generator: str


class CandidateGenerator(ABC):
    """Produces ordered candidate inputs from the sanitized, source-free context."""

    name: str

    @abstractmethod
    def generate(
        self, context: HypothesisContext, survivors: tuple[SurvivorHint, ...]
    ) -> Iterable[GeneratedCandidate]:
        raise NotImplementedError


class PermutationGenerator(CandidateGenerator):
    """Reorder each instructor case to expose input-order blind spots."""

    name = "permutation"

    def generate(
        self, context: HypothesisContext, survivors: tuple[SurvivorHint, ...]
    ) -> Iterable[GeneratedCandidate]:
        del survivors
        ordered = sorted(
            context.existing_tests, key=lambda item: (len(set(item.inputs)), item.label)
        )
        for test in ordered:
            for permuted in sorted(set(permutations(test.inputs))):
                if permuted != test.inputs:
                    yield GeneratedCandidate(
                        inputs=permuted,
                        rationale=f"Permute the instructor case labelled '{test.label}'.",
                        misconception="input-order blind spot",
                        generator=self.name,
                    )
                    anchor = max(context.domain_min, min(context.domain_max, 1))
                    for index, value in enumerate(permuted):
                        if permuted.count(value) != 1 or value == anchor:
                            continue
                        shrunk = list(permuted)
                        shrunk[index] = anchor
                        yield GeneratedCandidate(
                            inputs=tuple(shrunk),
                            rationale=(
                                "Preserve the equality pattern while shrinking its distinct input."
                            ),
                            misconception="order and magnitude interaction",
                            generator=self.name,
                        )


class BoundaryGenerator(CandidateGenerator):
    """Combine declared domain boundaries across every input position."""

    name = "boundary"

    def generate(
        self, context: HypothesisContext, survivors: tuple[SurvivorHint, ...]
    ) -> Iterable[GeneratedCandidate]:
        del survivors
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
            yield GeneratedCandidate(
                inputs=tuple(values),
                rationale="Combine declared domain boundaries across every input position.",
                misconception="boundary interaction",
                generator=self.name,
            )


class GeneratorScheduler(HypothesisProvider):
    """Compose generators under one candidate budget, deduplicating globally with provenance.

    Runs generators in order and takes their candidates first-come, skipping any input already
    proposed (by an instructor test or an earlier generator) so the shared budget is never spent
    twice on the same input. Each surviving candidate carries the name of the generator that
    produced it, so a finding's origin is auditable and each generator's contribution is measurable.
    """

    mode = "deterministic-fallback"

    def __init__(self, generators: Sequence[CandidateGenerator], budget: int = 8) -> None:
        if budget < 1:
            raise ValueError("candidate budget must be at least one")
        self.generators = tuple(generators)
        self.budget = budget

    def propose(
        self, context: HypothesisContext, survivors: tuple[SurvivorHint, ...]
    ) -> tuple[AttackHypothesis, ...]:
        seen = {test.inputs for test in context.existing_tests}
        selected: list[GeneratedCandidate] = []
        for generator in self.generators:
            if len(selected) >= self.budget:
                break
            for candidate in generator.generate(context, survivors):
                if candidate.inputs in seen:
                    continue
                seen.add(candidate.inputs)
                selected.append(candidate)
                if len(selected) >= self.budget:
                    break
        return tuple(
            AttackHypothesis(
                id=f"hypothesis-{index + 1}",
                inputs=candidate.inputs,
                rationale=candidate.rationale,
                misconception=candidate.misconception,
                provider="deterministic-fallback",
                generator=candidate.generator,
            )
            for index, candidate in enumerate(selected)
        )


def deterministic_scheduler(budget: int = 8) -> GeneratorScheduler:
    """The default composition: permutations first, then boundary combinations.

    Reproduces :class:`~coursefuzz.adapters.hypotheses.DeterministicHypothesisProvider` candidate
    for candidate, now attributable per generator.
    """
    return GeneratorScheduler((PermutationGenerator(), BoundaryGenerator()), budget=budget)
