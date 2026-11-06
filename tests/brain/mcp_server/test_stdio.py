"""End-to-end over the real stdio transport, as Claude Code launches it (no model involved)."""

import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def test_stdio_server_serves_memory_tools(tmp_path: Path) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "jarvis.brain.mcp_server"],
        env={"JARVIS_DATA_DIR": str(tmp_path / "data"), "PATH": "/usr/bin:/bin"},
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        names = {t.name for t in (await session.list_tools()).tools}
        assert "memory_write" in names and "memory_forget" not in names
        await session.call_tool("memory_write", {"path": "profile.md", "content": "Soham"})
        result = await session.call_tool("memory_read", {"path": "profile.md"})
        assert result.content[0].text == "Soham"
        bad = await session.call_tool("memory_read", {"path": "../etc/passwd"})
        assert bad.is_error
    assert (tmp_path / "data" / "memory" / "profile.md").read_text() == "Soham"
