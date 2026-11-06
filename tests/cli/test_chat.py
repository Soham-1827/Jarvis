import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from jarvis.cli.chat import ChatError, load_token, run_session


class FakeConn:
    def __init__(self, replies: list[dict]) -> None:
        self.sent: list[dict] = []
        self._replies = [json.dumps(r) for r in replies]

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def recv(self) -> str:
        if not self._replies:
            raise ConnectionError("closed")
        return self._replies.pop(0)


async def _lines(*items: str) -> AsyncIterator[str]:
    for item in items:
        yield item


async def test_session_auths_then_chats_and_keeps_conversation() -> None:
    conn = FakeConn(
        [
            {"type": "ready", "device": "laptop"},
            {"type": "thinking"},
            {"type": "reply", "conversation_id": "c9", "text": "Good evening, sir.", "ok": True},
            {"type": "thinking"},
            {"type": "reply", "conversation_id": "c9", "text": "Indeed.", "ok": True},
        ]
    )
    out: list[str] = []
    await run_session(conn, "tok", _lines("hello", "  ", "and again"), out.append)
    assert conn.sent[0] == {"type": "auth", "token": "tok"}
    assert conn.sent[1] == {"type": "chat", "text": "hello", "conversation_id": None}
    assert conn.sent[2] == {"type": "chat", "text": "and again", "conversation_id": "c9"}
    assert "Good evening, sir." in out and "Indeed." in out
    assert len(conn.sent) == 3  # blank line skipped


async def test_auth_failure_raises() -> None:
    conn = FakeConn([{"type": "error", "message": "invalid token"}])
    with pytest.raises(ChatError, match="invalid token"):
        await run_session(conn, "bad", _lines("hi"), lambda _: None)


async def test_server_error_is_shown_not_fatal() -> None:
    conn = FakeConn(
        [
            {"type": "ready", "device": "laptop"},
            {"type": "thinking"},
            {"type": "error", "message": "slow down: too many messages this minute"},
        ]
    )
    out: list[str] = []
    await run_session(conn, "tok", _lines("hi"), out.append)
    assert any("slow down" in line for line in out)


def test_load_token_prefers_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_TOKEN", "from-env")
    assert load_token(tmp_path / "token") == "from-env"


def test_load_token_from_private_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JARVIS_TOKEN", raising=False)
    path = tmp_path / "token"
    path.write_text("from-file\n")
    path.chmod(0o600)
    assert load_token(path) == "from-file"


def test_load_token_refuses_world_readable_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("JARVIS_TOKEN", raising=False)
    path = tmp_path / "token"
    path.write_text("x")
    path.chmod(0o644)
    with pytest.raises(ChatError, match="permissions"):
        load_token(path)


def test_load_token_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JARVIS_TOKEN", raising=False)
    with pytest.raises(ChatError, match="no token"):
        load_token(tmp_path / "absent")
