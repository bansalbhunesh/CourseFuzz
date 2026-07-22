import argparse
import json
import logging
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

TABLES = [
    "schema_migrations",
    "assignments",
    "assignment_access",
    "runs",
    "events",
    "approvals",
    "artifacts",
    "outbox_events",
]


def backup(dsn: str, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for table in TABLES:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()

            # Serialize bytes to hex for JSON compatibility
            for row in rows:
                for k, v in row.items():
                    if isinstance(v, bytes):
                        row[k] = {"__type__": "bytes", "hex": v.hex()}
                    elif v is not None and not isinstance(v, (str, int, float, bool, dict, list)):
                        row[k] = str(v)

            out_file = output_dir / f"{table}.json"
            out_file.write_text(json.dumps(rows, indent=2))
            logger.info(f"Backed up {len(rows)} rows from {table}")


def restore(dsn: str, input_dir: Path) -> None:
    if not input_dir.exists():
        logger.error(f"Backup directory not found: {input_dir}")
        sys.exit(1)

    with psycopg.connect(dsn) as conn, conn.transaction():
        # Disable triggers/constraints for bulk load
        conn.execute("SET session_replication_role = 'replica'")

        for table in reversed(TABLES):
            conn.execute(f"TRUNCATE {table} CASCADE")
            logger.info(f"Truncated {table}")

        for table in TABLES:
            in_file = input_dir / f"{table}.json"
            if not in_file.exists():
                logger.warning(f"No backup found for {table}, skipping.")
                continue

            rows = json.loads(in_file.read_text())
            if not rows:
                continue

            columns = list(rows[0].keys())
            col_names = ", ".join(columns)
            placeholders = ", ".join(["%s"] * len(columns))

            query = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})"

            for row in rows:
                values = []
                for col in columns:
                    val = row[col]
                    if isinstance(val, dict) and val.get("__type__") == "bytes":
                        values.append(bytes.fromhex(val["hex"]))
                    else:
                        values.append(val)
                conn.execute(query, values)

            logger.info(f"Restored {len(rows)} rows to {table}")

        conn.execute("SET session_replication_role = 'origin'")


def main() -> None:
    parser = argparse.ArgumentParser(description="CourseFuzz Backup and Restore Drill")
    parser.add_argument("--dsn", required=True, help="Postgres connection string")
    parser.add_argument("--action", choices=["backup", "restore"], required=True)
    parser.add_argument("--dir", type=Path, default=Path("backup_data"), help="Backup directory")
    args = parser.parse_args()

    if args.action == "backup":
        backup(args.dsn, args.dir)
    else:
        restore(args.dsn, args.dir)


if __name__ == "__main__":
    main()
