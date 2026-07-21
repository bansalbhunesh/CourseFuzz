from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluations.real_corpus import load_manifest  # noqa: E402

MANIFEST = ROOT / "evaluations" / "real" / "selection_manifest.json"
EXCLUSIONS = ROOT / "evaluations" / "real" / "exclusions.jsonl"


def main() -> int:
    manifest = load_manifest(MANIFEST)
    exclusions = [json.loads(line) for line in EXCLUSIONS.read_text(encoding="utf-8").splitlines()]
    selected_rows = {task.global_row_index for task in manifest.tasks}
    excluded_rows = {int(item["global_row_index"]) for item in exclusions}
    if selected_rows & excluded_rows:
        raise SystemExit("a selected row also appears in the exclusion ledger")
    scoped_rows = len(selected_rows) + len(excluded_rows)
    if scoped_rows != 1710:
        raise SystemExit(f"expected 1710 accounted rows across five shards, found {scoped_rows}")
    if any(task.source not in {"ATCODER", "AIZU"} for task in manifest.tasks):
        raise SystemExit("manifest contains a source outside the CodeNet-origin allowlist")
    print(
        f"verified {len(manifest.tasks)} tasks, "
        f"{sum(len(task.wrong_programs) for task in manifest.tasks)} wrong programs, "
        f"and {len(exclusions)} exclusions ({manifest.selection_sha256})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
