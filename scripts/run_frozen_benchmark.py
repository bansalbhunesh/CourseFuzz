from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluations.runner import run_inference, verify_report, write_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the frozen CourseFuzz benchmark")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluations/results/frozen-deterministic.json"),
    )
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    report = run_inference()
    failures = verify_report(report)
    if not args.no_write:
        write_report(report, args.output)
    print(json.dumps(report["aggregate"], indent=2, sort_keys=True))
    if failures:
        print("\nFrozen benchmark verification failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("\nFrozen benchmark verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
