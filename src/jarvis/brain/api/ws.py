"""WebSocket chat endpoint. Auth is the first message; nothing else is served before it.

Notes:
- The device token is the real gate. The Tailscale-User-Login allowlist is defence in depth: a
  tailnet peer can't forge it (`tailscale serve` overwrites it), but a local process could.
- Frame size is enforced by uvicorn (`ws_max_size`, see brain/__main__.py) before buffering.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError

from jarvis.brain.api.limits import ApiLimits
from jarvis.brain.services import Services
from jarvis.brain.store.devices import Device
from jarvis.shared.config import Settings
from jarvis.shared.protocol import (
    AuthMsg,
    ChatMsg,
    ErrorMsg,
    ReadyMsg,
    ReplyMsg,
    ThinkingMsg,
    client_msg_adapter,
)

log = logging.getLogger(__name__)
router = APIRouter()

AUTH_TIMEOUT_S = 10.0
MAX_FRAME_CHARS = 32_000
CHANNEL = "phone"
_FRAME_ERRORS = (WebSocketDisconnect, KeyError, RuntimeError)  # KeyError: binary frame


def _same_origin(ws: WebSocket) -> bool:
    """Browsers always send Origin; it must match Host (blocks cross-site WebSocket hijacking)."""
    origin = ws.headers.get("origin")
    if origin is None:
        return True  # non-browser clients (terminal, laptop node)
    host = ws.headers.get("host", "").lower()
    return bool(host) and urlsplit(origin).netloc.lower() == host


def _who(ws: WebSocket) -> str:
    return ws.headers.get("tailscale-user-login") or (ws.client.host if ws.client else "?")


def _tailscale_ok(ws: WebSocket, settings: Settings) -> bool:
    if not settings.allowed_tailscale_users:
        return True
    return ws.headers.get("tailscale-user-login", "") in settings.allowed_tailscale_users


async def _send(ws: WebSocket, msg: ReadyMsg | ThinkingMsg | ReplyMsg | ErrorMsg) -> None:
    await ws.send_text(msg.model_dump_json())


async def _audit(services: Services, action: str, target: str) -> None:
    await asyncio.to_thread(services.audit.record, "api", action, target)


async def _reject(ws: WebSocket, message: str) -> None:
    with contextlib.suppress(*_FRAME_ERRORS):
        await _send(ws, ErrorMsg(message=message))
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)


async def _authenticate(ws: WebSocket, services: Services, limits: ApiLimits) -> Device | None:
    try:
        async with asyncio.timeout(AUTH_TIMEOUT_S):
            raw = await ws.receive_text()
        msg = client_msg_adapter.validate_json(raw)
    except (TimeoutError, ValidationError, *_FRAME_ERRORS):
        msg = None
    if not isinstance(msg, AuthMsg):
        await _reject(ws, "authenticate first")
        return None
    device = await asyncio.to_thread(services.devices.verify, msg.token)
    if device is None:
        if limits.failed_auths.allow():  # bounded, so failures can't flood the audit log
            await _audit(services, "auth_failed", _who(ws))
        await _reject(ws, "invalid token")
        return None
    await _audit(services, "auth_ok", f"{device.name} via {_who(ws)}")
    return device


async def _run_turn(ws: WebSocket, services: Services, limits: ApiLimits, msg: ChatMsg) -> None:
    async with limits.turns:
        try:
            turn = await services.orchestrator.handle_turn(
                msg.text, channel=CHANNEL, conversation_id=msg.conversation_id
            )
        except LookupError:
            await _send(ws, ErrorMsg(message="unknown conversation"))
            return
        except ValueError:
            await _send(ws, ErrorMsg(message="invalid message"))
            return
    await _send(
        ws,
        ReplyMsg(
            conversation_id=turn.conversation_id,
            text=turn.reply,
            ok=turn.ok,
            five_hour_utilization=turn.five_hour_utilization,
            seven_day_utilization=turn.seven_day_utilization,
        ),
    )


async def _on_frame(
    ws: WebSocket, services: Services, limits: ApiLimits, device: Device, raw: str
) -> asyncio.Task[None] | None:
    """Validate one frame; return a started turn task, or None after replying with an error."""
    try:
        msg = client_msg_adapter.validate_json(raw)
    except ValidationError:
        await _send(ws, ErrorMsg(message="malformed message"))
        return None
    if not isinstance(msg, ChatMsg):
        await _send(ws, ErrorMsg(message="already authenticated"))
        return None
    if not await asyncio.to_thread(services.devices.is_active, device.id):
        await _audit(services, "revoked_device_closed", device.name)
        await _reject(ws, "device revoked")
        raise WebSocketDisconnect(code=status.WS_1008_POLICY_VIOLATION)
    if not limits.allow_chat(device.id):
        await _audit(services, "rate_limited", device.name)
        await _send(ws, ErrorMsg(message="slow down: too many messages this minute"))
        return None
    if not limits.try_start(device.id):
        await _send(ws, ErrorMsg(message="still working on your last message"))
        return None
    await _send(ws, ThinkingMsg())
    task = asyncio.create_task(_run_turn(ws, services, limits, msg))
    task.add_done_callback(lambda _: limits.finish(device.id))
    return task


async def _serve(ws: WebSocket, services: Services, limits: ApiLimits, device: Device) -> None:
    """Keep reading frames while a turn runs, so a disconnect cancels the turn promptly."""
    turn: asyncio.Task[None] | None = None
    receive: asyncio.Task[str] = asyncio.create_task(ws.receive_text())
    try:
        while True:
            pending = {receive} | ({turn} if turn else set())
            done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            if turn in done:
                turn.result()  # surface unexpected errors
                turn = None
            if receive in done:
                raw = receive.result()  # raises on disconnect or binary frame
                receive = asyncio.create_task(ws.receive_text())
                started = await _on_frame(ws, services, limits, device, raw)
                turn = started or turn
    finally:
        for task in (receive, turn):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, *_FRAME_ERRORS):
                    await task


@router.websocket("/ws")
async def chat_socket(ws: WebSocket) -> None:
    settings: Settings = ws.app.state.settings
    services: Services = ws.app.state.services
    limits: ApiLimits = ws.app.state.limits
    if not _same_origin(ws) or not _tailscale_ok(ws, settings):
        await _audit(services, "connection_refused", _who(ws))
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await ws.accept()
    device = await _authenticate(ws, services, limits)
    if device is None:
        return
    await _send(ws, ReadyMsg(device=device.name))
    try:
        await _serve(ws, services, limits, device)
    except WebSocketDisconnect:
        return  # client went away (or was closed for policy); nothing more to send
    except (KeyError, RuntimeError):
        # Binary or otherwise unsupported frame: close explicitly so the client isn't left hanging.
        with contextlib.suppress(*_FRAME_ERRORS):
            await ws.close(code=status.WS_1003_UNSUPPORTED_DATA)
