import sqlite3
from pathlib import Path

import pytest

from jarvis.brain.store.db import MIGRATIONS, connect, current_version, migrate


def test_migrate_fresh_db_to_latest(tmp_path: Path) -> None:
    conn = connect(tmp_path / "j.db")
    migrate(conn)
    assert current_version(conn) == len(MIGRATIONS)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"conversations", "messages", "jobs", "approvals", "audit"} <= tables


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    conn = connect(tmp_path / "j.db")
    migrate(conn)
    migrate(conn)
    assert current_version(conn) == len(MIGRATIONS)


def test_connect_enables_wal_and_foreign_keys(tmp_path: Path) -> None:
    conn = connect(tmp_path / "j.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_db_file_is_private(tmp_path: Path) -> None:
    path = tmp_path / "j.db"
    connect(path).close()
    assert path.stat().st_mode & 0o077 == 0


def test_failed_migration_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = connect(tmp_path / "j.db")
    migrate(conn)
    bad = (*MIGRATIONS, "CREATE TABLE ok_table (id INTEGER); THIS IS NOT SQL;")
    monkeypatch.setattr("jarvis.brain.store.db.MIGRATIONS", bad)
    with pytest.raises(sqlite3.Error):
        migrate(conn)
    assert current_version(conn) == len(MIGRATIONS)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "ok_table" not in tables


def test_open_database_migrates(tmp_path: Path) -> None:
    from jarvis.brain.store.db import open_database

    db = open_database(tmp_path / "j.db")
    with db.read() as conn:
        assert current_version(conn) == len(MIGRATIONS)
    db.close()
