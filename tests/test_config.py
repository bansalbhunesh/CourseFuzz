from __future__ import annotations

import pytest

from coursefuzz.config import analysis_deadline_seconds


def test_hosted_analysis_deadline_defaults_to_sixty_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COURSEFUZZ_ANALYSIS_DEADLINE_SECONDS", raising=False)

    assert analysis_deadline_seconds() == 60.0


@pytest.mark.parametrize("value", ["29", "121", "not-a-number"])
def test_analysis_deadline_fails_fast_outside_safe_bounds(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("COURSEFUZZ_ANALYSIS_DEADLINE_SECONDS", value)

    with pytest.raises(RuntimeError, match="between 30 and 120"):
        analysis_deadline_seconds()
