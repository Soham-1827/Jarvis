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
    with pytest.raises(ToolError, match="not a memory path") as exc:
        await server.call_tool("memory_write", {"path": path, "content": "x"})
    assert path not in str(exc.value)  # fixed message, nothing echoed back


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
    import sys

    from jarvis.brain.mcp_server.server import write_mcp_config

    path = write_mcp_config(tmp_path, data_dir=tmp_path / "data", python=sys.executable)
    config = json.loads(path.read_text())
    server = config["mcpServers"]["jarvis"]
    assert server["command"] == sys.executable
    assert server["args"] == ["-I", "-m", "jarvis.brain.mcp_server"]
    assert server["env"] == {"JARVIS_DATA_DIR": str(tmp_path / "data")}
    assert path.stat().st_mode & 0o077 == 0


def test_mcp_config_rejects_relative_python(tmp_path: Path) -> None:
    from jarvis.brain.mcp_server.server import write_mcp_config

    with pytest.raises(ValueError, match="absolute"):
        write_mcp_config(tmp_path, data_dir=tmp_path, python="python3")


def test_mcp_config_replaces_planted_symlink(tmp_path: Path) -> None:
    import sys

    from jarvis.brain.mcp_server.server import write_mcp_config

    target = tmp_path / "victim.txt"
    target.write_text("untouched")
    (tmp_path / "mcp.json").symlink_to(target)
    path = write_mcp_config(tmp_path, data_dir=tmp_path, python=sys.executable)
    assert not path.is_symlink()
    assert target.read_text() == "untouched"


async def test_search_audit_stores_fingerprint_not_query(parts) -> None:
    server, _, audit = parts
    await server.call_tool("memory_search", {"query": "my medical appointment"})
    target = audit.recent()[0].target
    assert "medical" not in target and target.startswith("sha256:")


async def test_call_budget_limits_rate(parts, monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis.brain.mcp_server import server as server_mod

    monkeypatch.setattr(server_mod, "CALLS_PER_MINUTE", 2)
    db_server = server_mod.build_server(parts[1], parts[2])
    await db_server.call_tool("memory_list", {})
    await db_server.call_tool("memory_list", {})
    with pytest.raises(ToolError, match="rate limit"):
        await db_server.call_tool("memory_list", {})


async def test_unexpected_errors_are_generic_and_audited(parts, monkeypatch) -> None:
    server, memory, audit = parts

    def explode(path: str) -> str:
        raise OSError("/var/lib/jarvis/memory/secret path detail")

    monkeypatch.setattr(memory, "read", explode)
    with pytest.raises(ToolError, match="internal error") as exc:
        await server.call_tool("memory_read", {"path": "profile.md"})
    assert "/var/lib" not in str(exc.value)
    assert audit.recent()[0].detail == {"ok": False}


async def test_long_paths_are_truncated_in_audit(parts) -> None:
    server, _, audit = parts
    with pytest.raises(ToolError):
        await server.call_tool("memory_read", {"path": "a" * 10_000})
    assert len(audit.recent()[0].target) == 200
