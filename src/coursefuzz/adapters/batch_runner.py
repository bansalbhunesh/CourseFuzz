"""Runs a batch of restricted-language programs in one process — one container == one batch.

Container start-up dominates per-execution cost, so a container-backed analysis should run many
programs per container, not one. This entrypoint reads a ``{"batch": [ {source, entrypoint, tests},
... ]}`` payload and returns one result per program, reusing ``coursefuzz.adapters.runner.run`` so
each program's validation and execution semantics are identical to the single-program path.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from coursefuzz.adapters.runner import run


def run_batch(payload: dict[str, Any]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for item in payload["batch"]:
        try:
            results.append({"ok": True, **run(item)})
        except ValueError as exc:
            # A restricted-language contract violation for this one program; the batch continues.
            results.append(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}", "kind": "rejected"}
            )
        except Exception as exc:
            results.append({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return {"ok": True, "results": results}


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
        sys.stdout.write(json.dumps(run_batch(payload), separators=(",", ":")))
        return 0
    except Exception as exc:
        sys.stdout.write(
            json.dumps(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
