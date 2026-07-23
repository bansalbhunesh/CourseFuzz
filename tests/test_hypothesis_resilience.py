from __future__ import annotations

import sys
import threading
import time
import types

from coursefuzz.adapters.hypotheses import (
    DeterministicHypothesisProvider,
    HypothesisBatch,
    HypothesisContext,
    HypothesisProvider,
    ModelHypothesis,
    OpenAIHypothesisProvider,
    ResilientHypothesisProvider,
    SurvivorHint,
)
from coursefuzz.domain.models import AttackHypothesis


class _UnavailableProvider(HypothesisProvider):
    mode = "live-gpt-5.6"

    def propose(self, context, survivors):  # type: ignore[override]
        del context, survivors
        raise TimeoutError("model deadline")


class _BlockingProvider(HypothesisProvider):
    mode = "live-gpt-5.6"

    def __init__(self, release: threading.Event) -> None:
        self.release = release

    def propose(self, context, survivors):  # type: ignore[override]
        del context, survivors
        self.release.wait(timeout=1.0)
        return ()


class _SuccessfulProvider(HypothesisProvider):
    mode = "live-gpt-5.6"

    def propose(self, context, survivors):  # type: ignore[override]
        del context, survivors
        return (
            AttackHypothesis(
                id="model-candidate",
                inputs=(2,),
                rationale="Probe the upper boundary.",
                misconception="misses the upper boundary",
                provider="gpt-5.6",
            ),
        )


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


def test_wall_timeout_returns_fallback_without_waiting_for_network_unwind() -> None:
    release = threading.Event()
    provider = ResilientHypothesisProvider(
        _BlockingProvider(release),
        DeterministicHypothesisProvider(),
        primary_wall_seconds=0.01,
        max_concurrent_primary_calls=1,
    )

    started = time.monotonic()
    hypotheses = provider.propose(
        _context(),
        (SurvivorHint(id="wrong", misconception="misses a boundary"),),
    )
    elapsed = time.monotonic() - started
    release.set()

    assert elapsed < 0.2
    assert hypotheses
    assert {item.provider for item in hypotheses} == {"deterministic-fallback"}


def test_successful_model_batch_keeps_deterministic_guardrail_candidates() -> None:
    provider = ResilientHypothesisProvider(
        _SuccessfulProvider(),
        DeterministicHypothesisProvider(),
    )

    hypotheses = provider.propose(
        _context(),
        (SurvivorHint(id="wrong", misconception="misses a boundary"),),
    )

    assert len(hypotheses) <= 8
    assert len({item.id for item in hypotheses}) == len(hypotheses)
    assert len({item.inputs for item in hypotheses}) == len(hypotheses)
    assert hypotheses[0].provider == "gpt-5.6"
    assert "deterministic-fallback" in {item.provider for item in hypotheses}


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


def test_openai_hypothesis_step_uses_low_reasoning_effort() -> None:
    captured: dict[str, object] = {}

    class _FakeCompletions:
        def parse(self, **kwargs):
            captured.update(kwargs)
            return types.SimpleNamespace(
                choices=[
                    types.SimpleNamespace(
                        message=types.SimpleNamespace(
                            parsed=HypothesisBatch(
                                hypotheses=[
                                    ModelHypothesis(
                                        inputs=(1,),
                                        rationale="Probe the untested boundary.",
                                        misconception="Misses a boundary.",
                                    )
                                ]
                            )
                        )
                    )
                ]
            )

    class _FakeChat:
        completions = _FakeCompletions()

    provider = object.__new__(OpenAIHypothesisProvider)
    provider.client = types.SimpleNamespace(chat=_FakeChat())
    provider.model = "gpt-5.6-sol"

    hypotheses = provider.propose(
        _context(),
        (SurvivorHint(id="wrong", misconception="misses a boundary"),),
    )

    assert hypotheses[0].provider == "gpt-5.6"
    assert captured["reasoning"] == {"effort": "low"}
