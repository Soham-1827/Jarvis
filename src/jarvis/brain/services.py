"""Wiring: build the brain's long-lived services from settings."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Protocol

from jarvis.brain.core.orchestrator import Orchestrator, Turn
from jarvis.brain.mcp_server.server import write_mcp_config
from jarvis.brain.memory.memory import Memory
from jarvis.brain.store.db import Database, open_database
from jarvis.brain.store.devices import DeviceRepo
from jarvis.brain.store.repos import AuditRepo
from jarvis.shared.config import Settings


class TurnHandler(Protocol):
    async def handle_turn(
        self, text: str, *, channel: str, conversation_id: str | None = None
    ) -> Turn: ...


@dataclass(frozen=True)
class Services:
    db: Database
    devices: DeviceRepo
    audit: AuditRepo
    orchestrator: TurnHandler

    def close(self) -> None:
        self.db.close()


def build_services(settings: Settings) -> Services:  # pragma: no cover - exercised on the server
    db = open_database(settings.db_path)
    memory = Memory.open(settings.memory_dir, db)
    mcp_config = write_mcp_config(
        settings.data_dir / "run", data_dir=settings.data_dir, python=sys.executable
    )
    work_dir = settings.data_dir / "orchestrator"
    work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    orchestrator = Orchestrator(
        db=db,
        memory=memory,
        mcp_config=mcp_config,
        claude_bin=settings.claude_bin,
        model=settings.orchestrator_model,
        timeout_s=settings.turn_timeout_s,
        cwd=work_dir,
    )
    return Services(db=db, devices=DeviceRepo(db), audit=AuditRepo(db), orchestrator=orchestrator)
