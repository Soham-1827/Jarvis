"""Paired devices (phone, laptop, terminal). Tokens are shown once and stored only as hashes."""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from jarvis.brain.store.db import Database

_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


@dataclass(frozen=True, slots=True)
class Device:
    id: str
    name: str
    created_at: str
    last_seen: str | None
    revoked_at: str | None


def _hash(token: str) -> str:
    # Tokens carry 256 bits of randomness, so a fast hash is enough (no password stretching).
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _device(row: sqlite3.Row) -> Device:
    data = dict(row)
    data.pop("token_hash", None)
    return Device(**data)


class DeviceRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, name: str) -> tuple[Device, str]:
        """Pair a device. Returns the device and its token; the token is never stored."""
        if not _NAME.fullmatch(name):
            raise ValueError("device name must be lowercase letters, digits and dashes")
        token = secrets.token_urlsafe(32)
        try:
            with self._db.tx() as conn:
                row = conn.execute(
                    "INSERT INTO devices (id, name, token_hash, created_at) VALUES (?, ?, ?, ?)"
                    " RETURNING *",
                    (uuid.uuid4().hex, name, _hash(token), _now()),
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"device {name!r} already exists") from exc
        return _device(row), token

    def verify(self, token: str) -> Device | None:
        """The active device for this token, or None. Updates last_seen on success."""
        if not token:
            return None
        with self._db.tx() as conn:
            row = conn.execute(
                "UPDATE devices SET last_seen = ? WHERE token_hash = ? AND revoked_at IS NULL"
                " RETURNING *",
                (_now(), _hash(token)),
            ).fetchone()
        return _device(row) if row else None

    def is_active(self, device_id: str) -> bool:
        """Cheap re-check so a revoked device loses an already-open connection."""
        with self._db.read() as conn:
            row = conn.execute(
                "SELECT 1 FROM devices WHERE id = ? AND revoked_at IS NULL", (device_id,)
            ).fetchone()
        return row is not None

    def revoke(self, name: str) -> bool:
        with self._db.tx() as conn:
            cur = conn.execute(
                "UPDATE devices SET revoked_at = ? WHERE name = ? AND revoked_at IS NULL",
                (_now(), name),
            )
        return cur.rowcount == 1

    def list(self) -> list[Device]:
        with self._db.read() as conn:
            rows = conn.execute("SELECT * FROM devices ORDER BY created_at, name").fetchall()
        return [_device(r) for r in rows]
