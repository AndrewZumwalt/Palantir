"""SQLite database backup + retention utility.

Uses SQLite's online backup API so backups are safe to run while services
are reading/writing. Supports gzip compression and keeps the N most recent
backups, deleting older ones.

Designed to be invoked on a schedule (systemd timer or cron). A typical
Palantir deployment runs this nightly:

    palantir-backup             # one-shot backup + rotation
"""

from __future__ import annotations

import argparse
import gzip
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import structlog

from palantir.config import PalantirConfig, load_config
from palantir.logging import setup_logging

logger = structlog.get_logger()


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def create_backup(config: PalantirConfig) -> Path | None:
    """Create a fresh snapshot of the live database.

    Returns the path to the new backup file, or None on failure.
    """
    src_path = Path(config.db_path)
    if not src_path.exists():
        logger.error("backup_source_missing", path=str(src_path))
        return None

    backup_dir = Path(config.backup.directory)
    backup_dir.mkdir(parents=True, exist_ok=True)

    ts = _timestamp()
    snapshot_path = backup_dir / f"palantir-{ts}.db"

    start = time.monotonic()
    # Use SQLite's online backup API — safe to run while the DB is open by
    # other processes. It takes a consistent snapshot at the row level.
    src = sqlite3.connect(str(src_path))
    try:
        dst = sqlite3.connect(str(snapshot_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    elapsed = time.monotonic() - start
    size_bytes = snapshot_path.stat().st_size

    final_path = snapshot_path
    if config.backup.compress:
        compressed = snapshot_path.with_suffix(".db.gz")
        with open(snapshot_path, "rb") as f_in, gzip.open(
            compressed, "wb", compresslevel=6
        ) as f_out:
            shutil.copyfileobj(f_in, f_out)
        snapshot_path.unlink()
        final_path = compressed
        size_bytes = final_path.stat().st_size

    logger.info(
        "backup_created",
        path=str(final_path),
        size_bytes=size_bytes,
        elapsed_seconds=round(elapsed, 2),
    )
    return final_path


def rotate_backups(config: PalantirConfig) -> int:
    """Delete backups older than the retention window.

    Returns the number of files deleted.
    """
    backup_dir = Path(config.backup.directory)
    if not backup_dir.exists():
        return 0

    # Match both compressed and uncompressed
    backups = sorted(
        list(backup_dir.glob("palantir-*.db"))
        + list(backup_dir.glob("palantir-*.db.gz")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    keep = max(1, config.backup.keep_last_n)
    to_delete = backups[keep:]

    for old in to_delete:
        try:
            old.unlink()
            logger.info("backup_rotated", path=str(old))
        except OSError:
            logger.exception("backup_rotation_failed", path=str(old))

    return len(to_delete)


def verify_backup(backup_path: Path) -> bool:
    """Quick sanity check that a backup file is a valid SQLite database."""
    source: Path = backup_path

    if backup_path.suffix == ".gz":
        # Decompress to a temp file to verify
        tmp = backup_path.with_suffix("")
        with gzip.open(backup_path, "rb") as f_in, open(tmp, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        source = tmp

    try:
        conn = sqlite3.connect(str(source))
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()
            ok = result is not None and result[0] == "ok"
            if not ok:
                logger.error("backup_integrity_check_failed", path=str(backup_path), result=result)
            return ok
        finally:
            conn.close()
    finally:
        if source != backup_path and source.exists():
            source.unlink()


def run_backup(verify: bool = True) -> int:
    """Full backup flow: snapshot, verify, rotate. Returns shell exit code."""
    config = load_config()
    if not config.backup.enabled:
        logger.info("backup_disabled_in_config")
        return 0

    path = create_backup(config)
    if path is None:
        return 1

    if verify and not verify_backup(path):
        logger.error("backup_verification_failed", path=str(path))
        return 2

    deleted = rotate_backups(config)
    logger.info("backup_complete", new=str(path), rotated=deleted)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Palantir database backup")
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip integrity check on the new backup",
    )
    parser.add_argument(
        "--rotate-only",
        action="store_true",
        help="Just prune old backups; do not create a new one",
    )
    args = parser.parse_args()

    setup_logging("backup")

    if args.rotate_only:
        deleted = rotate_backups(load_config())
        logger.info("rotation_complete", deleted=deleted)
        sys.exit(0)

    sys.exit(run_backup(verify=not args.no_verify))


if __name__ == "__main__":
    main()
