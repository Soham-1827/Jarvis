"""`python -m jarvis.brain.mcp_server`: run the Jarvis MCP server on stdio for `claude -p`."""

from __future__ import annotations

import logging

from jarvis.brain.mcp_server.server import build_server
from jarvis.brain.memory.memory import Memory
from jarvis.brain.store.db import open_database
from jarvis.brain.store.repos import AuditRepo
from jarvis.shared.config import load_settings


def main() -> (
    None
):  # pragma: no cover - runs in a subprocess (see tests/brain/mcp_server/test_stdio.py)
    # stdout is the MCP channel: logs must go to stderr only.
    logging.basicConfig(level=logging.WARNING)
    settings = load_settings()
    db = open_database(settings.db_path)
    memory = Memory.open(settings.memory_dir, db)
    build_server(memory, AuditRepo(db)).run("stdio")


if __name__ == "__main__":
    main()
