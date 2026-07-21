from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluations.real_corpus import CodeContestsSource, load_manifest  # noqa: E402
from evaluations.real_scoring import build_public_bundle, seal_candidates  # noqa: E402

MANIFEST = ROOT / "evaluations" / "real" / "selection_manifest.json"
DEFAULT_CACHE = ROOT / ".cache" / "coursefuzz-evaluation" / "codecontests"
DEFAULT_BUNDLE = ROOT / ".cache" / "coursefuzz-evaluation" / "public-bundle.jsonl"
DEFAULT_RECEIPT = ROOT / ".cache" / "coursefuzz-evaluation" / "candidate-receipt.json"


def _bundle(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest)
    source = CodeContestsSource(args.cache_dir)
    rows = {task.global_row_index: source.row(task.global_row_index) for task in manifest.tasks}
    digest = build_public_bundle(manifest, rows, args.output)
    print(json.dumps({"public_bundle": str(args.output), "sha256": digest}, indent=2))
    return 0


def _seal(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest)
    receipt = seal_candidates(
        manifest,
        args.public_bundle,
        args.candidates,
        args.receipt,
        budget_per_task=args.budget,
    )
    print(json.dumps(receipt.model_dump(mode="json"), indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare leakage-resistant real evaluation files")
    subparsers = parser.add_subparsers(dest="command", required=True)

    bundle = subparsers.add_parser("bundle", help="materialize provider-visible context only")
    bundle.add_argument("--manifest", type=Path, default=MANIFEST)
    bundle.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    bundle.add_argument("--output", type=Path, default=DEFAULT_BUNDLE)
    bundle.set_defaults(handler=_bundle)

    seal = subparsers.add_parser("seal", help="freeze equal-budget candidates before scoring")
    seal.add_argument("--manifest", type=Path, default=MANIFEST)
    seal.add_argument("--public-bundle", type=Path, default=DEFAULT_BUNDLE)
    seal.add_argument("--candidates", type=Path, required=True)
    seal.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    seal.add_argument("--budget", type=int, required=True)
    seal.set_defaults(handler=_seal)

    args = parser.parse_args()
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
