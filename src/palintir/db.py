"""SQLite database connection and migration management."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import structlog

from palintir.config import PalintirConfig

logger = structlog.get_logger()

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def get_connection(config: PalintirConfig) -> sqlite3.Connection:
    """Create a SQLite connection with WAL mode enabled."""
    db_path = Path(config.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def get_current_version(conn: sqlite3.Connection) -> int:
    """Get the current schema version, or 0 if no schema exists."""
    try:
        row = conn.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()
        return row[0] or 0
    except sqlite3.OperationalError:
        return 0


def run_migrations(conn: sqlite3.Connection) -> None:
    """Apply all pending SQL migrations in order."""
    current = get_current_version(conn)

    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    for migration_file in migration_files:
        # Extract version number from filename (e.g., "001_initial_schema.sql" -> 1)
        version = int(migration_file.stem.split("_")[0])
        if version <= current:
            continue

        logger.info("applying_migration", version=version, file=migration_file.name)
        sql = migration_file.read_text()
        conn.executescript(sql)

    logger.info("migrations_complete", version=get_current_version(conn))


def init_db(config: PalintirConfig) -> sqlite3.Connection:
    """Initialize the database: create connection and run migrations."""
    conn = get_connection(config)
    run_migrations(conn)
    return conn
