from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jarvis.brain.app import create_app
from jarvis.brain.core.orchestrator import Turn
from jarvis.brain.services import Services
from jarvis.brain.store.db import open_database
from jarvis.brain.store.devices import DeviceRepo
from jarvis.brain.store.repos import AuditRepo
from jarvis.shared.config import Settings


@dataclass
class FakeOrchestrator:
    calls: list[tuple[str, str, str | None]] = field(default_factory=list)

    async def handle_turn(self, text: str, *, channel: str, conversation_id: str | None = None):
        self.calls.append((text, channel, conversation_id))
        if conversation_id == "missing":
            raise LookupError("conversation missing not found")
        return Turn(conversation_id or "c1", f"echo: {text}", ok=True, five_hour_utilization=0.2)


@pytest.fixture
def env(tmp_path: Path):
    db = open_database(tmp_path / "j.db")
    devices = DeviceRepo(db)
    _, token = devices.add("iphone")
    orch = FakeOrchestrator()
    services = Services(db=db, devices=devices, audit=AuditRepo(db), orchestrator=orch)
    app = create_app(Settings(data_dir=tmp_path), services=services)
    return TestClient(app), token, orch, services


def _auth(ws, token: str) -> dict:
    ws.send_json({"type": "auth", "token": token})
    return ws.receive_json()


def test_auth_then_chat_roundtrip(env) -> None:
    client, token, orch, _ = env
    with client.websocket_connect("/ws") as ws:
        assert _auth(ws, token) == {"type": "ready", "device": "iphone"}
        ws.send_json({"type": "chat", "text": "Hello"})
        assert ws.receive_json()["type"] == "thinking"
        reply = ws.receive_json()
        assert reply["type"] == "reply" and reply["text"] == "echo: Hello"
        assert reply["conversation_id"] == "c1" and reply["five_hour_utilization"] == 0.2
    assert orch.calls == [("Hello", "phone", None)]


def test_bad_token_is_rejected_and_audited(env) -> None:
    client, _, orch, services = env
    with client.websocket_connect("/ws") as ws:
        assert _auth(ws, "nope")["type"] == "error"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 1008
    assert orch.calls == []
    assert services.audit.recent()[0].action == "auth_failed"


def test_chat_before_auth_is_rejected(env) -> None:
    client, _, orch, _ = env
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "chat", "text": "sneaky"})
        assert ws.receive_json()["type"] == "error"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert orch.calls == []


def test_cross_origin_connection_is_refused(env) -> None:
    client, _, _, _ = env
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect("/ws", headers={"origin": "https://evil.example"}),
    ):
        pass
    assert exc.value.code == 1008


def test_same_origin_is_allowed(env) -> None:
    client, token, _, _ = env
    with client.websocket_connect("/ws", headers={"origin": "http://testserver"}) as ws:
        assert _auth(ws, token)["type"] == "ready"


def test_malformed_and_oversized_messages_get_errors(env) -> None:
    client, token, _, _ = env
    with client.websocket_connect("/ws") as ws:
        _auth(ws, token)
        ws.send_text("{not json")
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "chat", "text": "x" * 30_000})
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "chat", "text": "still alive"})
        assert ws.receive_json()["type"] == "thinking"
        assert ws.receive_json()["text"] == "echo: still alive"


def test_unknown_conversation_is_an_error_not_a_crash(env) -> None:
    client, token, _, _ = env
    with client.websocket_connect("/ws") as ws:
        _auth(ws, token)
        ws.send_json({"type": "chat", "text": "hi", "conversation_id": "missing"})
        ws.receive_json()  # thinking
        err = ws.receive_json()
        assert err == {"type": "error", "message": "unknown conversation"}


def test_rate_limit_per_connection(env, monkeypatch: pytest.MonkeyPatch) -> None:
    client, token, _, _ = env
    client.app.state.limits.chats_per_minute = 1
    with client.websocket_connect("/ws") as ws:
        _auth(ws, token)
        ws.send_json({"type": "chat", "text": "one"})
        ws.receive_json()
        ws.receive_json()
        ws.send_json({"type": "chat", "text": "two"})
        assert "slow down" in ws.receive_json()["message"]


def test_tailscale_identity_allowlist(tmp_path: Path) -> None:
    db = open_database(tmp_path / "j.db")
    devices = DeviceRepo(db)
    _, token = devices.add("iphone")
    services = Services(
        db=db, devices=devices, audit=AuditRepo(db), orchestrator=FakeOrchestrator()
    )
    settings = Settings(data_dir=tmp_path, allowed_tailscale_users=("soham@github",))
    client = TestClient(create_app(settings, services=services))
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/ws", headers={"tailscale-user-login": "stranger@github"}),
    ):
        pass
    with client.websocket_connect("/ws", headers={"tailscale-user-login": "soham@github"}) as ws:
        assert _auth(ws, token)["type"] == "ready"


def test_revoked_device_loses_open_connection(env) -> None:
    client, token, orch, services = env
    with client.websocket_connect("/ws") as ws:
        _auth(ws, token)
        services.devices.revoke("iphone")
        ws.send_json({"type": "chat", "text": "still there?"})
        assert ws.receive_json() == {"type": "error", "message": "device revoked"}
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert orch.calls == []


def test_binary_frame_closes_quietly(env) -> None:
    client, token, _, _ = env
    with client.websocket_connect("/ws") as ws:
        _auth(ws, token)
        ws.send_bytes(b"\x00\x01")
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_successful_auth_is_audited_with_identity(env) -> None:
    client, token, _, services = env
    with client.websocket_connect("/ws", headers={"tailscale-user-login": "soham@github"}) as ws:
        _auth(ws, token)
    entry = services.audit.recent()[0]
    assert entry.action == "auth_ok" and entry.target == "iphone via soham@github"


def test_failed_auth_audit_is_bounded(env) -> None:
    client, _, _, services = env
    for _ in range(15):
        with client.websocket_connect("/ws") as ws:
            _auth(ws, "bad")
    failures = [e for e in services.audit.recent() if e.action == "auth_failed"]
    assert len(failures) == 10


def test_origin_without_host_is_refused() -> None:
    from types import SimpleNamespace

    from jarvis.brain.api.ws import _same_origin

    fake = SimpleNamespace(headers={"origin": "null"})
    assert _same_origin(fake) is False  # type: ignore[arg-type]
