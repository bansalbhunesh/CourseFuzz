from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from coursefuzz.repositories.migrator import run_migrations  # noqa: E402


def main() -> None:
    print("Running CourseFuzz database migrations...")
    applied = run_migrations()
    print(f"Migrations complete. Applied: {applied}")


if __name__ == "__main__":
    main()
