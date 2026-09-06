"""Forward-only SQLite migration runner."""
from hashlib import sha256
from pathlib import Path
import sqlite3

from app.domain.errors import MigrationError


class MigrationRunner:
    def __init__(self, database_path: Path, migrations_path: Path | None = None) -> None:
        self._database_path = database_path
        self._migrations_path = migrations_path or Path(__file__).parents[2] / "migrations"

    def apply(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._database_path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    checksum TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            for path in sorted(self._migrations_path.glob("*.sql")):
                version = path.name
                script = path.read_text(encoding="utf-8")
                checksum = sha256(script.encode("utf-8")).hexdigest()
                existing = connection.execute(
                    "SELECT checksum FROM schema_migrations WHERE version = ?", (version,)
                ).fetchone()
                if existing:
                    if existing[0] != checksum:
                        raise MigrationError(f"migration checksum changed: {version}")
                    continue
                connection.executescript(script)
                connection.execute(
                    "INSERT INTO schema_migrations(version, checksum, applied_at) "
                    "VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                    (version, checksum),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
