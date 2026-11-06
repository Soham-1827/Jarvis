"""Repositories over the brain's SQLite state. All access goes through `Database` (locked)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from jarvis.brain.store.db import Database
from jarvis.brain.store.records import (
    CHANNELS,
    JOB_FINAL,
    JOB_STATUSES,
    ROLES,
    Approval,
    AuditEntry,
    Conversation,
    Job,
    Message,
    approval_from,
    audit_from,
    conversation_from,
    job_from,
    message_from,
)

DEFAULT_APPROVAL_TTL = timedelta(hours=1)
MAX_LIMIT = 500


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _new_id() -> str:
    return uuid.uuid4().hex


def _require(value: str, allowed: frozenset[str], field: str) -> None:
    if value not in allowed:
        raise ValueError(f"invalid {field} {value!r}; expected one of {sorted(allowed)}")


def _limit(limit: int) -> int:
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    return limit


class ConversationRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, channel: str) -> Conversation:
        _require(channel, CHANNELS, "channel")
        now = _now()
        with self._db.tx() as conn:
            row = conn.execute(
                "INSERT INTO conversations (id, channel, created_at, updated_at)"
                " VALUES (?, ?, ?, ?) RETURNING *",
                (_new_id(), channel, now, now),
            ).fetchone()
        return conversation_from(row)

    def get(self, convo_id: str) -> Conversation | None:
        with self._db.read() as conn:
            row = conn.execute("SELECT * FROM conversations WHERE id = ?", (convo_id,)).fetchone()
        return conversation_from(row) if row else None

    def set_session(self, convo_id: str, session_id: str) -> Conversation:
        with self._db.tx() as conn:
            row = conn.execute(
                "UPDATE conversations SET claude_session_id = ?, updated_at = ?"
                " WHERE id = ? RETURNING *",
                (session_id, _now(), convo_id),
            ).fetchone()
        if row is None:
            raise LookupError(f"conversation {convo_id} not found")
        return conversation_from(row)


class MessageRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(
        self,
        conversation_id: str,
        role: str,
        content: str,
        *,
        cost_usd: float | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
    ) -> Message:
        _require(role, ROLES, "role")
        try:
            with self._db.tx() as conn:
                row = conn.execute(
                    "INSERT INTO messages (conversation_id, role, content, cost_usd, tokens_in,"
                    " tokens_out, created_at) VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING *",
                    (conversation_id, role, content, cost_usd, tokens_in, tokens_out, _now()),
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"unknown conversation {conversation_id}") from exc
        return message_from(row)

    def list(self, conversation_id: str, limit: int = 50) -> list[Message]:
        """The latest `limit` messages, oldest first."""
        with self._db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM (SELECT * FROM messages WHERE conversation_id = ?"
                " ORDER BY id DESC LIMIT ?) ORDER BY id",
                (conversation_id, _limit(limit)),
            ).fetchall()
        return [message_from(r) for r in rows]


class JobRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create(
        self,
        kind: str,
        task: str,
        repo: str | None = None,
        model: str | None = None,
        conversation_id: str | None = None,
    ) -> Job:
        with self._db.tx() as conn:
            row = conn.execute(
                "INSERT INTO jobs (id, conversation_id, kind, task, repo, model, status,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?) RETURNING *",
                (_new_id(), conversation_id, kind, task, repo, model, _now()),
            ).fetchone()
        return job_from(row)

    def get(self, job_id: str) -> Job | None:
        with self._db.read() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return job_from(row) if row else None

    def set_status(
        self, job_id: str, status: str, *, result: str | None = None, error: str | None = None
    ) -> Job:
        """Move a job to `status`. Jobs in a final state never change again."""
        _require(status, JOB_STATUSES, "status")
        now = _now()
        final = sorted(JOB_FINAL)
        with self._db.tx() as conn:
            row = conn.execute(
                "UPDATE jobs SET status = ?,"
                " result = COALESCE(?, result), error = COALESCE(?, error),"
                " started_at = COALESCE(started_at, CASE WHEN ? = 'running' THEN ? END),"
                " finished_at = CASE WHEN ? IN (?, ?, ?) THEN ? ELSE finished_at END"
                " WHERE id = ? AND status NOT IN (?, ?, ?) RETURNING *",
                (status, result, error, status, now, status, *final, now, job_id, *final),
            ).fetchone()
            current = None
            if row is None:
                current = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            if current is None:
                raise LookupError(f"job {job_id} not found")
            raise ValueError(f"job {job_id} is already {current['status']}")
        return job_from(row)

    def list(self, status: str | None = None, limit: int = 50) -> list[Job]:
        """Newest first."""
        if status is not None:
            _require(status, JOB_STATUSES, "status")
        with self._db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE (? IS NULL OR status = ?) ORDER BY seq DESC LIMIT ?",
                (status, status, _limit(limit)),
            ).fetchall()
        return [job_from(r) for r in rows]


class ApprovalRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create(
        self,
        kind: str,
        summary: str,
        payload: dict[str, Any],
        job_id: str | None = None,
        ttl: timedelta = DEFAULT_APPROVAL_TTL,
    ) -> Approval:
        now = datetime.now(UTC)
        with self._db.tx() as conn:
            row = conn.execute(
                "INSERT INTO approvals (id, job_id, kind, summary, payload_json, status,"
                " created_at, expires_at) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?) RETURNING *",
                (
                    _new_id(), job_id, kind, summary, json.dumps(payload),
                    now.isoformat(timespec="milliseconds"),
                    (now + ttl).isoformat(timespec="milliseconds"),
                ),
            ).fetchone()  # fmt: skip
        return approval_from(row)

    def get(self, approval_id: str) -> Approval | None:
        with self._db.read() as conn:
            row = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        return approval_from(row) if row else None

    def pending(self) -> list[Approval]:
        """Live pending approvals, oldest first. Expires stale ones first."""
        self.expire_stale()
        with self._db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM approvals WHERE status = 'pending' ORDER BY seq"
            ).fetchall()
        return [approval_from(r) for r in rows]

    def expire_stale(self) -> int:
        with self._db.tx() as conn:
            cur = conn.execute(
                "UPDATE approvals SET status = 'expired'"
                " WHERE status = 'pending' AND expires_at <= ?",
                (_now(),),
            )
        return cur.rowcount

    def decide(self, approval_id: str, *, approved: bool, decided_by: str) -> Approval:
        """Approve or deny exactly once, before expiry, in a single guarded statement."""
        if not decided_by.strip():
            raise ValueError("decided_by is required")
        status = "approved" if approved else "denied"
        now = _now()
        with self._db.tx() as conn:
            row = conn.execute(
                "UPDATE approvals SET status = ?, decided_at = ?, decided_by = ?"
                " WHERE id = ? AND status = 'pending' AND expires_at > ? RETURNING *",
                (status, now, decided_by, approval_id, now),
            ).fetchone()
            current = None
            if row is None:
                conn.execute(
                    "UPDATE approvals SET status = 'expired'"
                    " WHERE id = ? AND status = 'pending' AND expires_at <= ?",
                    (approval_id, now),
                )
                current = conn.execute(
                    "SELECT status FROM approvals WHERE id = ?", (approval_id,)
                ).fetchone()
        if row is None:
            if current is None:
                raise LookupError(f"approval {approval_id} not found")
            raise ValueError(f"approval {approval_id} is already {current['status']}")
        return approval_from(row)


class AuditRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def record(
        self,
        actor: str,
        action: str,
        target: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        with self._db.tx() as conn:
            conn.execute(
                "INSERT INTO audit (ts, actor, action, target, detail_json) VALUES (?, ?, ?, ?, ?)",
                (_now(), actor, action, target, json.dumps(detail) if detail is not None else None),
            )

    def recent(self, limit: int = 100) -> list[AuditEntry]:
        """Newest first."""
        with self._db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (_limit(limit),)
            ).fetchall()
        return [audit_from(r) for r in rows]
