from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from jarvis.brain.mcp_server.server import TOOL_NAMES, build_server
from jarvis.brain.memory.memory import Memory
from jarvis.brain.store.db import open_database
from jarvis.brain.store.repos import AuditRepo


@pytest.fixture
def parts(tmp_path: Path):
    db = open_database(tmp_path / "j.db")
    memory = Memory.open(tmp_path / "mem", db)
    return build_server(memory, AuditRepo(db)), memory, AuditRepo(db)


def _text(result) -> str:
    return result.content[0].text


async def test_lists_exactly_the_memory_tools(parts) -> None:
    server, _, _ = parts
    names = {t.name for t in await server.list_tools()}
    assert names == set(TOOL_NAMES)
    assert "memory_forget" not in names  # deleting data is never an automatic tool


async def test_write_then_read_and_search(parts) -> None:
    server, memory, _ = parts
    await server.call_tool(
        "memory_write", {"path": "areas/jarvis.md", "content": "Home server M700"}
    )
    assert memory.read("areas/jarvis.md") == "Home server M700"
    assert _text(await server.call_tool("memory_read", {"path": "areas/jarvis.md"})) == (
        "Home server M700"
    )
    found = _text(await server.call_tool("memory_search", {"query": "server"}))
    assert "areas/jarvis.md" in found


async def test_read_missing_says_so(parts) -> None:
    server, _, _ = parts
    assert "no memory file" in _text(await server.call_tool("memory_read", {"path": "profile.md"}))


async def test_append_and_list(parts) -> None:
    server, _, _ = parts
    await server.call_tool("memory_append", {"path": "log/2026-11-05.md", "line": "- shipped 1.3"})
    listing = _text(await server.call_tool("memory_list", {}))
    assert "log/2026-11-05.md" in listing


async def test_search_with_no_hits(parts) -> None:
    server, _, _ = parts
    assert "no matches" in _text(await server.call_tool("memory_search", {"query": "zebra"}))


@pytest.mark.parametrize("path", ["../etc/passwd", "/etc/shadow", "secrets.md"])
async def test_bad_paths_become_tool_errors(parts, path: str) -> None:
    server, _, _ = parts
    with pytest.raises(ToolError, match="not a memory path"):
        await server.call_tool("memory_write", {"path": path, "content": "x"})


async def test_every_call_is_audited_without_content(parts) -> None:
    server, _, audit = parts
    await server.call_tool("memory_write", {"path": "profile.md", "content": "private details"})
    with pytest.raises(ToolError):
        await server.call_tool("memory_read", {"path": "../x"})
    entries = audit.recent()
    assert [(e.action, e.target) for e in entries] == [
        ("memory_read", "../x"),
        ("memory_write", "profile.md"),
    ]
    assert entries[0].detail == {"ok": False}
    assert all("private details" not in str(e.detail) for e in entries)


def test_mcp_config_points_at_this_server(tmp_path: Path) -> None:
    import json

    from jarvis.brain.mcp_server.server import write_mcp_config

    path = write_mcp_config(tmp_path, data_dir=tmp_path / "data", python="/opt/py")
    config = json.loads(path.read_text())
    server = config["mcpServers"]["jarvis"]
    assert server["command"] == "/opt/py"
    assert server["args"] == ["-m", "jarvis.brain.mcp_server"]
    assert server["env"] == {"JARVIS_DATA_DIR": str(tmp_path / "data")}
    assert path.stat().st_mode & 0o077 == 0
