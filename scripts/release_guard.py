from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES = (
    "LICENSE",
    "README.md",
    "compose.yaml",
    "render.yaml",
    "docs/ARCHITECTURE.md",
    "docs/DEMO_RUNBOOK.md",
    "docs/DEPLOYMENT.md",
    "docs/EDGE_CASE_MATRIX.md",
    "docs/EVALUATION.md",
    "docs/SECURITY.md",
    "evaluations/frozen_expectations.json",
)
PUBLIC_FIELDS = (
    "public_repository_url",
    "public_demo_url",
    "video_url",
    "live_github_receipt_url",
)


def _is_public_https_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.hostname
        not in {
            "example.com",
            "localhost",
            "127.0.0.1",
        }
    )


def check_release(
    root: Path = ROOT,
    *,
    require_submission: bool = False,
) -> list[str]:
    failures = [name for name in REQUIRED_FILES if not (root / name).is_file()]
    manifest_path = root / "release_manifest.json"
    if not manifest_path.is_file():
        return [*failures, "release_manifest.json is missing"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("category") != "Education":
        failures.append("release category must remain Education unless intentionally reviewed")

    expected = json.loads(
        (root / "evaluations" / "frozen_expectations.json").read_text(encoding="utf-8")
    )
    report = json.loads((root / manifest["frozen_benchmark"]["report"]).read_text(encoding="utf-8"))
    declared_digest = manifest["frozen_benchmark"].get("corpus_sha256")
    if declared_digest != expected.get("corpus_sha256"):
        failures.append("release manifest benchmark digest differs from frozen expectations")
    if report.get("corpus_sha256") != declared_digest:
        failures.append("committed benchmark report does not match the release manifest")

    if require_submission:
        if manifest.get("status") != "submission-ready":
            failures.append("release status is not submission-ready")
        for field in PUBLIC_FIELDS:
            if not _is_public_https_url(manifest.get(field)):
                failures.append(f"{field} is missing or is not a public HTTPS URL")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Check CourseFuzz release evidence")
    parser.add_argument(
        "--submission",
        action="store_true",
        help="require public app, video, repository, and live GitHub proof",
    )
    args = parser.parse_args()
    failures = check_release(require_submission=args.submission)
    if failures:
        print("CourseFuzz release guard failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("CourseFuzz repository evidence is internally consistent.")
    if not args.submission:
        print("Submission-only public evidence was not required by this check.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
