"""SQLite connection, serialized access and forward-only migrations for the brain's state."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# Append-only once deployed: never edit a shipped migration, add a new one.
# (Migration 1 has not shipped to a server yet, so it is still being shaped.)
MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE conversations (
        id                TEXT PRIMARY KEY,
        channel           TEXT NOT NULL CHECK (channel IN ('phone', 'laptop_voice', 'terminal',
                                                           'scheduled')),
        claude_session_id TEXT,
        created_at        TEXT NOT NULL,
        updated_at        TEXT NOT NULL
    );
    CREATE TABLE messages (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        role            TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
        content         TEXT NOT NULL,
        cost_usd        REAL,
        tokens_in       INTEGER,
        tokens_out      INTEGER,
        created_at      TEXT NOT NULL
    );
    CREATE INDEX idx_messages_conversation ON messages(conversation_id, id);
    CREATE TABLE jobs (
        seq             INTEGER PRIMARY KEY AUTOINCREMENT,
        id              TEXT NOT NULL UNIQUE,
        conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
        kind            TEXT NOT NULL,
        task            TEXT NOT NULL,
        repo            TEXT,
        model           TEXT,
        status          TEXT NOT NULL CHECK (status IN ('queued', 'running', 'waiting',
                                                        'succeeded', 'failed', 'cancelled')),
        result          TEXT,
        error           TEXT,
        attempts        INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT NOT NULL,
        started_at      TEXT,
        heartbeat_at    TEXT,
        finished_at     TEXT
    );
    CREATE INDEX idx_jobs_status ON jobs(status, seq);
    CREATE TABLE approvals (
        seq          INTEGER PRIMARY KEY AUTOINCREMENT,
        id           TEXT NOT NULL UNIQUE,
        job_id       TEXT REFERENCES jobs(id) ON DELETE SET NULL,
        kind         TEXT NOT NULL,
        summary      TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        status       TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'denied', 'expired')),
        created_at   TEXT NOT NULL,
        expires_at   TEXT NOT NULL,
        decided_at   TEXT,
        decided_by   TEXT
    );
    CREATE INDEX idx_approvals_status ON approvals(status, seq);
    CREATE TABLE audit (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          TEXT NOT NULL,
        actor       TEXT NOT NULL,
        action      TEXT NOT NULL,
        target      TEXT,
        detail_json TEXT
    );
    CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit
    BEGIN
        SELECT RAISE(ABORT, 'audit log is append-only');
    END;
    CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit
    BEGIN
        SELECT RAISE(ABORT, 'audit log is append-only');
    END;
    CREATE VIRTUAL TABLE memory_fts USING fts5(
        path UNINDEXED, content, tokenize='porter unicode61'
    );
    """,
)


class Database:
    """One SQLite connection shared by the brain, with every access serialized by a lock.

    Repos are synchronous and may be called from `asyncio.to_thread`; the lock makes that safe.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.RLock()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            yield self._conn

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Exclusive write transaction: commits on success, rolls back on any exception."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def connect(path: Path) -> sqlite3.Connection:
    """Open the DB owner-only with WAL and foreign keys, in autocommit mode."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    if mode != "wal" or conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        conn.close()
        raise sqlite3.OperationalError("could not enable WAL and foreign keys")
    return conn


def open_database(path: Path) -> Database:
    conn = connect(path)
    migrate(conn)
    return Database(conn)


def current_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection) -> None:
    """Apply pending migrations, each atomically with its version bump.

    The version is re-read inside the write lock, so two processes starting together
    cannot both apply the same migration.
    """
    for version, script in enumerate(MIGRATIONS, start=1):
        conn.execute("BEGIN IMMEDIATE")
        try:
            if current_version(conn) < version:
                for statement in _split(script):
                    conn.execute(statement)
                conn.execute(f"PRAGMA user_version = {version:d}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise


def _split(script: str) -> list[str]:
    """Split a migration into complete statements (executescript would auto-commit)."""
    statements: list[str] = []
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statements.append(buffer)
            buffer = ""
    if buffer.strip():
        statements.append(buffer)  # incomplete trailing SQL: let execute() raise on it
    return statements
