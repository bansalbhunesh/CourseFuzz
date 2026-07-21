from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluations.real_corpus import (  # noqa: E402
    CodeContestsSource,
    collect_manifest,
    load_manifest,
    write_collection,
)

DEFAULT_MANIFEST = ROOT / "evaluations" / "real" / "selection_manifest.json"
DEFAULT_EXCLUSIONS = ROOT / "evaluations" / "real" / "exclusions.jsonl"
DEFAULT_CACHE = ROOT / ".cache" / "coursefuzz-evaluation" / "codecontests"


def _compare(expected: Path, observed: Path) -> None:
    if expected.read_bytes() != observed.read_bytes():
        raise SystemExit(f"frozen artifact drifted: {expected}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect the pinned non-vendored real corpus")
    parser.add_argument("--check", action="store_true", help="regenerate and compare with Git")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--exclusions", type=Path, default=DEFAULT_EXCLUSIONS)
    args = parser.parse_args()

    source = CodeContestsSource(args.cache_dir)
    manifest, exclusions = collect_manifest(source)
    if args.check:
        with tempfile.TemporaryDirectory(prefix="coursefuzz-real-check-") as temp:
            temp_dir = Path(temp)
            observed_manifest = temp_dir / "selection_manifest.json"
            observed_exclusions = temp_dir / "exclusions.jsonl"
            write_collection(manifest, exclusions, observed_manifest, observed_exclusions)
            _compare(args.manifest, observed_manifest)
            _compare(args.exclusions, observed_exclusions)
    else:
        write_collection(manifest, exclusions, args.manifest, args.exclusions)

    loaded = load_manifest(args.manifest)
    print(
        json.dumps(
            {
                "corpus": loaded.corpus_id,
                "tasks": len(loaded.tasks),
                "wrong_programs": sum(len(task.wrong_programs) for task in loaded.tasks),
                "accepted_controls": sum(len(task.accepted_controls) for task in loaded.tasks),
                "selection_sha256": loaded.selection_sha256,
                "review_status": loaded.second_review_status,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
