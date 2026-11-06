"""`jarvis chat`: talk to the brain from a terminal over the same WebSocket API as the phone.

    JARVIS_URL=wss://<server>.ts.net/ws jarvis chat
Token: $JARVIS_TOKEN, or ~/.config/jarvis/token (must be mode 600).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Protocol

DEFAULT_URL = "ws://127.0.0.1:8765/ws"
DEFAULT_TOKEN_FILE = Path.home() / ".config" / "jarvis" / "token"


class ChatError(RuntimeError):
    pass


class Conn(Protocol):
    async def send(self, data: str) -> None: ...
    async def recv(self) -> str: ...


def load_token(path: Path = DEFAULT_TOKEN_FILE) -> str:
    if token := os.environ.get("JARVIS_TOKEN", "").strip():
        return token
    if not path.is_file():
        raise ChatError(f"no token: set JARVIS_TOKEN or write it to {path} (chmod 600)")
    if path.stat().st_mode & 0o077:
        raise ChatError(f"{path} has loose permissions; run: chmod 600 {path}")
    return path.read_text().strip()


async def _next(conn: Conn) -> dict:
    return json.loads(await conn.recv())


async def run_session(
    conn: Conn, token: str, lines: AsyncIterator[str], out: Callable[[str], None]
) -> None:
    await conn.send(json.dumps({"type": "auth", "token": token}))
    first = await _next(conn)
    if first.get("type") != "ready":
        raise ChatError(f"authentication failed: {first.get('message', 'unknown error')}")
    out(f"(connected as {first['device']}; Ctrl-D to leave)")
    conversation: str | None = None
    async for line in lines:
        text = line.strip()
        if not text:
            continue
        await conn.send(json.dumps({"type": "chat", "text": text, "conversation_id": conversation}))
        while True:
            msg = await _next(conn)
            if msg["type"] == "thinking":
                continue
            if msg["type"] == "reply":
                conversation = msg["conversation_id"]
                out(msg["text"])
            else:
                out(f"[{msg.get('message', 'error')}]")
            break


async def _stdin_lines() -> AsyncIterator[str]:  # pragma: no cover - interactive
    while True:
        try:
            line = await asyncio.to_thread(input, "you> ")
        except EOFError:
            return
        yield line


async def _main() -> None:  # pragma: no cover - needs a live brain
    import websockets

    url = os.environ.get("JARVIS_URL", DEFAULT_URL)
    async with websockets.connect(url, max_size=1_000_000) as conn:
        await run_session(conn, load_token(), _stdin_lines(), lambda s: print(f"jarvis> {s}"))  # noqa: T201


def main() -> int:  # pragma: no cover - interactive
    try:
        asyncio.run(_main())
    except (ChatError, OSError) as exc:
        print(f"jarvis: {exc}", file=sys.stderr)  # noqa: T201
        return 1
    except KeyboardInterrupt:
        return 0
    return 0
