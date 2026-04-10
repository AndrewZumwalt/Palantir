"""Shared test fixtures for Palintir."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

# Force development environment for tests
os.environ["PALINTIR_ENV"] = "development"


@pytest.fixture
def temp_db():
    """Create a temporary SQLite database with schema applied."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    # Apply initial schema
    migrations_dir = Path(__file__).parent.parent / "src" / "palintir" / "migrations"
    for migration_file in sorted(migrations_dir.glob("*.sql")):
        conn.executescript(migration_file.read_text())

    yield conn

    conn.close()
    os.unlink(db_path)


@pytest.fixture
def config():
    """Load test configuration."""
    from palintir.config import load_config

    return load_config("development")
