"""Immutable records returned by the store, plus the allowed values for enum-like columns."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

CHANNELS = frozenset({"phone", "laptop_voice", "terminal", "scheduled"})
ROLES = frozenset({"user", "assistant", "system"})
JOB_STATUSES = frozenset({"queued", "running", "waiting", "succeeded", "failed", "cancelled"})
JOB_FINAL = frozenset({"succeeded", "failed", "cancelled"})


@dataclass(frozen=True, slots=True)
class Conversation:
    id: str
    channel: str
    claude_session_id: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class Message:
    id: int
    conversation_id: str
    role: str
    content: str
    cost_usd: float | None
    tokens_in: int | None
    tokens_out: int | None
    created_at: str


@dataclass(frozen=True, slots=True)
class Job:
    id: str
    conversation_id: str | None
    kind: str
    task: str
    repo: str | None
    model: str | None
    status: str
    result: str | None
    error: str | None
    attempts: int
    created_at: str
    started_at: str | None
    heartbeat_at: str | None
    finished_at: str | None


@dataclass(frozen=True, slots=True)
class Approval:
    id: str
    job_id: str | None
    kind: str
    summary: str
    payload: dict[str, Any]
    status: str
    created_at: str
    expires_at: str
    decided_at: str | None
    decided_by: str | None


@dataclass(frozen=True, slots=True)
class AuditEntry:
    id: int
    ts: str
    actor: str
    action: str
    target: str | None
    detail: dict[str, Any] | None


def conversation_from(row: sqlite3.Row) -> Conversation:
    return Conversation(**dict(row))


def message_from(row: sqlite3.Row) -> Message:
    return Message(**dict(row))


def job_from(row: sqlite3.Row) -> Job:
    data = dict(row)
    data.pop("seq", None)
    return Job(**data)


def approval_from(row: sqlite3.Row) -> Approval:
    data = dict(row)
    data.pop("seq", None)
    data["payload"] = json.loads(data.pop("payload_json"))
    return Approval(**data)


def audit_from(row: sqlite3.Row) -> AuditEntry:
    data = dict(row)
    raw = data.pop("detail_json")
    return AuditEntry(**data, detail=json.loads(raw) if raw is not None else None)
