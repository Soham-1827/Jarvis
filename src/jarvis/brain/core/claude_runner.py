"""Run Claude Code headless (`claude -p`) and stream its stream-json events as typed objects.

Hardening:
- the prompt goes on stdin and the system prompt in a 0600 temp file, never in argv (`ps`, E2BIG);
- every flag uses `--flag=value`, so a variadic option can never swallow arguments;
- the child gets an allowlisted environment, not the brain's secrets;
- only user-level Claude settings load (`--setting-sources=user`), so a repo's `.claude/` can't
  change permissions or add hooks;
- the child runs in its own process group, killed as a group on timeout or cancellation.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import signal
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SAFE_PERMISSION_MODES = frozenset({"default", "acceptEdits", "plan"})
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TOOL = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
_STREAM_LIMIT = 2 * 1024 * 1024
_STDERR_TAIL = 4096
MAX_SYSTEM_PROMPT_CHARS = 200_000
MAX_TOTAL_TEXT_CHARS = 1_000_000
_CLEANUP_TIMEOUT_S = 5.0
# Only these reach the child. Claude Code auth lives in ~/.claude or CLAUDE_CODE_OAUTH_TOKEN.
ENV_ALLOWLIST = frozenset(
    {"PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TERM", "TZ", "CLAUDE_CODE_OAUTH_TOKEN"}
)


class ClaudeError(RuntimeError):
    """Claude Code failed, timed out or produced no result.

    `stderr` holds the CLI's stderr tail for debug logging; it is kept out of the message so it
    never reaches the phone or normal logs.
    """

    def __init__(self, message: str, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


def child_env(extra: tuple[tuple[str, str], ...] = ()) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in ENV_ALLOWLIST or k.startswith("XDG_")}
    env.update(dict(extra))
    return env


@dataclass(frozen=True, slots=True)
class ClaudeRequest:
    prompt: str
    claude_bin: str | None = None
    model: str | None = None
    resume_session: str | None = None
    append_system_prompt: str | None = None
    mcp_config: Path | None = None
    allowed_tools: tuple[str, ...] = ()
    permission_mode: str = "default"
    cwd: Path | None = None
    timeout_s: float = 300.0
    extra_env: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.append_system_prompt is not None and (
            "\x00" in self.append_system_prompt
            or len(self.append_system_prompt) > MAX_SYSTEM_PROMPT_CHARS
        ):
            raise ValueError("system prompt contains NUL or is too long")
        if "\x00" in self.prompt:
            raise ValueError("prompt contains NUL")
        if self.permission_mode not in SAFE_PERMISSION_MODES:
            raise ValueError(f"unsafe permission mode {self.permission_mode!r}")
        for name, value in (("model", self.model), ("resume_session", self.resume_session)):
            if value is not None and not _TOKEN.fullmatch(value):
                raise ValueError(f"invalid {name} {value!r}")
        for tool in self.allowed_tools:
            if not _TOOL.fullmatch(tool):
                raise ValueError(f"invalid tool name {tool!r}")


@dataclass(frozen=True, slots=True)
class InitEvent:
    session_id: str
    model: str


@dataclass(frozen=True, slots=True)
class TextEvent:
    text: str


@dataclass(frozen=True, slots=True)
class ToolUseEvent:
    name: str
    input: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RateLimitEvent:
    status: str
    five_hour_utilization: float | None
    seven_day_utilization: float | None
    resets_at: int | None


@dataclass(frozen=True, slots=True)
class ResultEvent:
    is_error: bool
    text: str
    session_id: str
    subtype: str
    cost_usd: float
    tokens_in: int
    tokens_out: int
    num_turns: int


Event = InitEvent | TextEvent | ToolUseEvent | RateLimitEvent | ResultEvent


@dataclass(frozen=True, slots=True)
class RunOutcome:
    result: ResultEvent
    texts: tuple[str, ...] = ()
    tool_uses: tuple[ToolUseEvent, ...] = ()
    rate_limit: RateLimitEvent | None = None
    model: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def resolve_binary(claude_bin: str | None) -> str:
    """An absolute, executable path to the CLI."""
    found = claude_bin or shutil.which("claude")
    if not found or not Path(found).is_absolute():
        raise ClaudeError(f"claude binary not found: {found or 'claude'}")
    if not os.access(found, os.X_OK):
        raise ClaudeError(f"claude binary not found or not executable: {found}")
    return found


def build_argv(
    req: ClaudeRequest, binary: str, system_prompt_file: Path | None = None
) -> list[str]:
    argv = [
        binary,
        "-p",
        "--output-format=stream-json",
        "--verbose",
        "--setting-sources=user",
        f"--permission-mode={req.permission_mode}",
    ]
    if req.model:
        argv.append(f"--model={req.model}")
    if req.resume_session:
        argv.append(f"--resume={req.resume_session}")
    if system_prompt_file is not None:
        argv.append(f"--append-system-prompt-file={system_prompt_file}")
    if req.mcp_config:
        argv += [f"--mcp-config={req.mcp_config}", "--strict-mcp-config"]
    if req.allowed_tools:
        argv.append(f"--allowedTools={','.join(req.allowed_tools)}")
    return argv


def _usage_int(usage: dict[str, Any], *keys: str) -> int:
    return sum(int(usage.get(k) or 0) for k in keys)


def _parse_assistant(data: dict[str, Any]) -> list[Event]:
    events: list[Event] = []
    for block in (data.get("message") or {}).get("content") or []:
        if block.get("type") == "text" and block.get("text"):
            events.append(TextEvent(block["text"]))
        elif block.get("type") == "tool_use":
            events.append(ToolUseEvent(str(block.get("name", "")), dict(block.get("input") or {})))
    return events


def _parse_rate_limit(data: dict[str, Any]) -> RateLimitEvent:
    info = data.get("rate_limit_info") or {}
    windows = info.get("unifiedWindows") or {}
    return RateLimitEvent(
        status=str(info.get("status", "unknown")),
        five_hour_utilization=(windows.get("five_hour") or {}).get("utilization"),
        seven_day_utilization=(windows.get("seven_day") or {}).get("utilization"),
        resets_at=info.get("resetsAt"),
    )


def _parse_result(data: dict[str, Any]) -> ResultEvent:
    usage = data.get("usage") or {}
    return ResultEvent(
        is_error=bool(data.get("is_error")),
        text=str(data.get("result") or ""),
        session_id=str(data.get("session_id") or ""),
        subtype=str(data.get("subtype") or ""),
        cost_usd=float(data.get("total_cost_usd") or 0.0),
        tokens_in=_usage_int(
            usage, "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"
        ),
        tokens_out=_usage_int(usage, "output_tokens"),
        num_turns=int(data.get("num_turns") or 0),
    )


def parse_events(line: str) -> list[Event]:
    """Parse one stream-json line. Unknown, noisy or malformed lines yield no events."""
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        if line.strip():
            log.warning("skipping non-JSON line from claude")
        return []
    if not isinstance(data, dict):
        return []
    kind = data.get("type")
    if kind == "system" and data.get("subtype") == "init":
        return [InitEvent(str(data.get("session_id", "")), str(data.get("model", "")))]
    if kind == "assistant":
        return _parse_assistant(data)
    if kind == "rate_limit_event":
        return [_parse_rate_limit(data)]
    if kind == "result":
        return [_parse_result(data)]
    return []


async def _tail(stream: asyncio.StreamReader) -> bytes:
    data = b""
    while chunk := await stream.read(65536):
        data = (data + chunk)[-_STDERR_TAIL:]
    return data


async def _kill_group(proc: asyncio.subprocess.Process) -> None:
    """Kill the child's whole process group (tools, MCP servers) and reap it, bounded in time."""
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(_CLEANUP_TIMEOUT_S):
            await proc.wait()


async def _spawn(argv: list[str], req: ClaudeRequest) -> asyncio.subprocess.Process:
    try:
        return await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=req.cwd,
            env=child_env(req.extra_env),
            limit=_STREAM_LIMIT,
            start_new_session=True,
        )
    except (FileNotFoundError, PermissionError) as exc:
        raise ClaudeError(f"claude binary not found: {argv[0]}") from exc


async def run_claude(req: ClaudeRequest) -> AsyncIterator[Event]:
    """Spawn `claude -p`, feed the prompt on stdin and yield events as they arrive.

    Consume with `contextlib.aclosing(...)` so an early exit kills the child promptly.
    """
    binary = resolve_binary(req.claude_bin)
    with tempfile.TemporaryDirectory(prefix="jarvis-claude-") as tmp:
        prompt_file = None
        if req.append_system_prompt:
            prompt_file = Path(tmp) / "system.md"
            prompt_file.touch(mode=0o600)
            prompt_file.write_text(req.append_system_prompt, encoding="utf-8")
        proc = await _spawn(build_argv(req, binary, prompt_file), req)
        assert proc.stdin and proc.stdout and proc.stderr
        stderr_task = asyncio.create_task(_tail(proc.stderr))
        saw_result = False
        stderr = ""
        try:
            async with asyncio.timeout(req.timeout_s):
                proc.stdin.write(req.prompt.encode())
                await proc.stdin.drain()
                proc.stdin.close()
                async for raw in proc.stdout:
                    for event in parse_events(raw.decode(errors="replace")):
                        saw_result = saw_result or isinstance(event, ResultEvent)
                        yield event
                code = await proc.wait()
        except TimeoutError as exc:
            raise ClaudeError(f"claude timed out after {req.timeout_s}s") from exc
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise ClaudeError("claude closed its input early") from exc
        except (ValueError, asyncio.LimitOverrunError) as exc:
            raise ClaudeError("claude output line exceeded the size limit") from exc
        finally:
            await asyncio.shield(_kill_group(proc))
            stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                stderr = (await stderr_task).decode(errors="replace").strip()
        if not saw_result:
            log.debug("claude stderr: %s", stderr)
            reason = f"exited with {code}" if code != 0 else "finished with no result event"
            raise ClaudeError(f"claude {reason}", stderr=stderr)


async def run_to_result(req: ClaudeRequest) -> RunOutcome:
    texts: list[str] = []
    text_chars = 0
    tools: list[ToolUseEvent] = []
    rate: RateLimitEvent | None = None
    model: str | None = None
    result: ResultEvent | None = None
    async with contextlib.aclosing(run_claude(req)) as events:
        async for event in events:
            match event:
                case InitEvent():
                    model = event.model
                case TextEvent() if text_chars < MAX_TOTAL_TEXT_CHARS:
                    texts.append(event.text)
                    text_chars += len(event.text)
                case ToolUseEvent() if len(tools) < 500:
                    tools.append(event)
                case RateLimitEvent():
                    rate = event
                case ResultEvent():
                    result = event
    assert result is not None  # run_claude raises when there is no result
    return RunOutcome(result, tuple(texts), tuple(tools), rate, model)
