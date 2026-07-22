from __future__ import annotations

import sys
import types

from coursefuzz.adapters.hypotheses import (
    DeterministicHypothesisProvider,
    HypothesisContext,
    HypothesisProvider,
    OpenAIHypothesisProvider,
    ResilientHypothesisProvider,
    SurvivorHint,
)


class _UnavailableProvider(HypothesisProvider):
    mode = "live-gpt-5.6"

    def propose(self, context, survivors):  # type: ignore[override]
        del context, survivors
        raise TimeoutError("model deadline")


def _context() -> HypothesisContext:
    return HypothesisContext(
        title="Bounded example",
        summary="Return a result for one bounded integer.",
        input_names=("value",),
        domain_min=0,
        domain_max=2,
        existing_tests=(),
    )


def test_timeout_uses_attributed_deterministic_fallback() -> None:
    provider = ResilientHypothesisProvider(
        _UnavailableProvider(),
        DeterministicHypothesisProvider(),
    )

    hypotheses = provider.propose(
        _context(),
        (SurvivorHint(id="wrong", misconception="misses a boundary"),),
    )

    assert hypotheses
    assert {item.provider for item in hypotheses} == {"deterministic-fallback"}


def test_openai_request_cannot_consume_the_oracle_deadline(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class _FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI))

    OpenAIHypothesisProvider()

    assert captured == {"timeout": 12.0, "max_retries": 0}
