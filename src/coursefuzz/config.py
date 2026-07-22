from __future__ import annotations

import os


def analysis_deadline_seconds() -> float:
    """Return the fail-fast, bounded end-to-end analysis budget."""

    raw = os.getenv("COURSEFUZZ_ANALYSIS_DEADLINE_SECONDS", "60")
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(
            "COURSEFUZZ_ANALYSIS_DEADLINE_SECONDS must be a number between 30 and 120"
        ) from exc
    if not 30 <= value <= 120:
        raise RuntimeError("COURSEFUZZ_ANALYSIS_DEADLINE_SECONDS must be between 30 and 120")
    return value
