"""The orchestrator: one user turn in, one Jarvis reply out, with memory and transcripts."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from jarvis.brain.core.claude_runner import (
    ClaudeError,
    ClaudeRequest,
    RateLimitEvent,
    RunOutcome,
    run_to_result,
)
from jarvis.brain.core.prompt import build_system_prompt
from jarvis.brain.mcp_server.server import MCP_SERVER_NAME, TOOL_NAMES
from jarvis.brain.memory.memory import Memory
from jarvis.brain.store.db import Database
from jarvis.brain.store.repos import AuditRepo, ConversationRepo, MessageRepo

log = logging.getLogger(__name__)

Runner = Callable[[ClaudeRequest], Awaitable[RunOutcome]]

# Phase 1: memory tools only. No built-in tools (no Read/Bash/WebFetch), so an injected prompt
# can neither read local files nor exfiltrate memory over the network.
ORCHESTRATOR_TOOLS = tuple(f"mcp__{MCP_SERVER_NAME}__{name}" for name in TOOL_NAMES)
DISALLOWED_TOOLS = ("Bash", "Read", "Write", "Edit", "WebFetch", "WebSearch", "Task")
APOLOGY = "I'm afraid I couldn't complete that just now, sir. Please try again in a moment."
MAX_MESSAGE_CHARS = 20_000


@dataclass(frozen=True, slots=True)
class Turn:
    conversation_id: str
    reply: str
    ok: bool
    cost_usd: float = 0.0
    five_hour_utilization: float | None = None
    seven_day_utilization: float | None = None


class Orchestrator:
    def __init__(
        self,
        *,
        db: Database,
        memory: Memory,
        mcp_config: Path,
        runner: Runner = run_to_result,
        claude_bin: str | None = None,
        model: str | None = None,
        timeout_s: float = 300.0,
        cwd: Path | None = None,
    ) -> None:
        self._conversations = ConversationRepo(db)
        self._messages = MessageRepo(db)
        self._audit = AuditRepo(db)
        self._memory = memory
        self._runner = runner
        self.mcp_config = mcp_config
        self._claude_bin = claude_bin
        self._model = model
        self._timeout_s = timeout_s
        self._cwd = cwd
        self._locks: dict[str, asyncio.Lock] = {}
        self.last_rate_limit: RateLimitEvent | None = None

    async def handle_turn(
        self, text: str, *, channel: str, conversation_id: str | None = None
    ) -> Turn:
        text = text.strip()
        if not text or len(text) > MAX_MESSAGE_CHARS:
            raise ValueError("message must be 1 to 20000 characters")
        convo = await self._conversation(conversation_id, channel)
        lock = self._locks.setdefault(convo.id, asyncio.Lock())
        async with lock:  # one turn at a time per conversation keeps the session linear
            await asyncio.to_thread(self._messages.add, convo.id, "user", text)
            current = await asyncio.to_thread(self._conversations.get, convo.id)
            session = current.claude_session_id if current else None
            outcome = await self._run(text, channel, session)
            return await self._record(convo.id, outcome)

    async def _conversation(self, conversation_id: str | None, channel: str):
        if conversation_id is None:
            return await asyncio.to_thread(self._conversations.create, channel)
        convo = await asyncio.to_thread(self._conversations.get, conversation_id)
        if convo is None:
            raise LookupError(f"conversation {conversation_id} not found")
        return convo

    def _request(self, text: str, channel: str, session: str | None) -> ClaudeRequest:
        hits = self._memory.search(text, limit=5)
        system = build_system_prompt(
            profile=self._memory.read("profile.md"),
            preferences=self._memory.read("preferences.md"),
            hits=hits,
            now=datetime.now().astimezone(),
            channel=channel,
        )
        return ClaudeRequest(
            prompt=text,
            claude_bin=self._claude_bin,
            model=self._model,
            resume_session=session,
            append_system_prompt=system,
            mcp_config=self.mcp_config,
            allowed_tools=ORCHESTRATOR_TOOLS,
            builtin_tools=(),
            disallowed_tools=DISALLOWED_TOOLS,
            permission_mode="default",
            cwd=self._cwd,
            timeout_s=self._timeout_s,
        )

    async def _run(self, text: str, channel: str, session: str | None) -> RunOutcome | None:
        request = await asyncio.to_thread(self._request, text, channel, session)
        try:
            return await self._runner(request)
        except ClaudeError as exc:
            if session is None:
                log.warning("claude turn failed: %s", exc)
                return None
            log.warning("resume of %s failed (%s); retrying in a fresh session", session, exc)
        try:
            return await self._runner(await asyncio.to_thread(self._request, text, channel, None))
        except ClaudeError as exc:
            log.warning("claude turn failed after fresh retry: %s", exc)
            return None

    async def _record(self, convo_id: str, outcome: RunOutcome | None) -> Turn:
        if outcome is None or outcome.result.is_error:
            subtype = outcome.result.subtype if outcome else "claude_error"
            await asyncio.to_thread(
                self._audit.record, "orchestrator", "turn_failed", convo_id, {"reason": subtype}
            )
            await asyncio.to_thread(
                self._messages.add, convo_id, "system", f"turn failed: {subtype}"
            )
            return Turn(convo_id, APOLOGY, ok=False)
        result = outcome.result
        if outcome.rate_limit is not None:
            self.last_rate_limit = outcome.rate_limit
        await asyncio.to_thread(self._conversations.set_session, convo_id, result.session_id)
        await asyncio.to_thread(
            lambda: self._messages.add(
                convo_id,
                "assistant",
                result.text,
                cost_usd=result.cost_usd,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
            )
        )
        rate = outcome.rate_limit
        return Turn(
            convo_id,
            result.text,
            ok=True,
            cost_usd=result.cost_usd,
            five_hour_utilization=rate.five_hour_utilization if rate else None,
            seven_day_utilization=rate.seven_day_utilization if rate else None,
        )
