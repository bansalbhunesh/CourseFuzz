import logging
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)


class Migrator:
    def __init__(self, dsn: str, migrations_dir: Path) -> None:
        self.dsn = dsn
        self.migrations_dir = migrations_dir

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def migrate(self) -> None:
        """Applies pending migrations in alphabetical order."""
        with self._connect() as connection:
            # 1. Create migration tracking table
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
                """
            )
            
            # 2. Find applied migrations
            rows = connection.execute("SELECT version FROM schema_migrations").fetchall()
            applied = {row["version"] for row in rows}

            # 3. Find available migrations
            if not self.migrations_dir.exists():
                logger.warning(f"Migrations directory not found: {self.migrations_dir}")
                return

            available = sorted(f.name for f in self.migrations_dir.glob("*.sql"))

            # 4. Apply new migrations sequentially with an advisory lock
            # Lock ID: 123456789 (arbitrary 64-bit int for schema migrations)
            connection.execute("SELECT pg_advisory_lock(123456789)")
            try:
                for filename in available:
                    if filename not in applied:
                        filepath = self.migrations_dir / filename
                        logger.info(f"Applying migration: {filename}")
                        with connection.transaction():
                            sql = filepath.read_text("utf-8")
                            connection.execute(sql)
                            connection.execute(
                                "INSERT INTO schema_migrations (version) VALUES (%s)", (filename,)
                            )
            finally:
                connection.execute("SELECT pg_advisory_unlock(123456789)")

