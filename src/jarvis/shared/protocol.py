"""Messages between clients (PWA, terminal, laptop node) and the brain over WebSocket."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

MAX_TEXT_CHARS = 20_000


class _Msg(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AuthMsg(_Msg):
    type: Literal["auth"] = "auth"
    token: str = Field(min_length=1, max_length=200)


class ChatMsg(_Msg):
    type: Literal["chat"] = "chat"
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    conversation_id: str | None = Field(default=None, max_length=64)


ClientMsg = Annotated[AuthMsg | ChatMsg, Field(discriminator="type")]
client_msg_adapter: TypeAdapter[AuthMsg | ChatMsg] = TypeAdapter(ClientMsg)


class ReadyMsg(_Msg):
    type: Literal["ready"] = "ready"
    device: str


class ThinkingMsg(_Msg):
    type: Literal["thinking"] = "thinking"


class ReplyMsg(_Msg):
    type: Literal["reply"] = "reply"
    conversation_id: str
    text: str
    ok: bool
    five_hour_utilization: float | None = None
    seven_day_utilization: float | None = None


class ErrorMsg(_Msg):
    type: Literal["error"] = "error"
    message: str


ServerMsg = Annotated[ReadyMsg | ThinkingMsg | ReplyMsg | ErrorMsg, Field(discriminator="type")]
server_msg_adapter: TypeAdapter[ReadyMsg | ThinkingMsg | ReplyMsg | ErrorMsg] = TypeAdapter(
    ServerMsg
)
