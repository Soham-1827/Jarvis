import json
import sys
from pathlib import Path

import pytest

from jarvis.brain.core.claude_runner import (
    ClaudeError,
    ClaudeRequest,
    InitEvent,
    RateLimitEvent,
    ResultEvent,
    TextEvent,
    ToolUseEvent,
    build_argv,
    parse_events,
    run_claude,
    run_to_result,
)

FIXTURES = Path(__file__).parents[2] / "fixtures"
FAKE = FIXTURES / "fake_claude.py"


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Route the runner to the fake CLI; returns where argv/stdin get recorded."""
    wrapper = tmp_path / "claude"
    wrapper.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} "$@"\n')
    wrapper.chmod(0o755)
    record = tmp_path / "argv.json"
    monkeypatch.setenv("FAKE_ARGV_OUT", str(record))
    monkeypatch.setenv("FAKE_FIXTURE", str(FIXTURES / "claude_stream_ok.jsonl"))
    monkeypatch.setenv("JARVIS_TEST_CLAUDE_BIN", str(wrapper))
    return record


def _req(**overrides: object) -> ClaudeRequest:
    import os

    # The runner strips the environment, so test controls go through extra_env explicitly.
    fake_env = tuple((k, v) for k, v in os.environ.items() if k.startswith("FAKE_"))
    base: dict[str, object] = {"prompt": "hello", "claude_bin": None, "extra_env": fake_env}
    base.update(overrides)
    return ClaudeRequest(**base)  # type: ignore[arg-type]


def _bin() -> str:
    import os

    return os.environ["JARVIS_TEST_CLAUDE_BIN"]


def test_build_argv_minimal() -> None:
    argv = build_argv(ClaudeRequest(prompt="hi"), "/usr/bin/claude")
    assert argv[:2] == ["/usr/bin/claude", "-p"]
    assert "--setting-sources=user" in argv
    assert "--output-format=stream-json" in argv
    assert "--verbose" in argv
    assert "--permission-mode=default" in argv
    assert "hi" not in argv  # prompt goes on stdin, never argv
    assert not any(a.startswith("--resume") for a in argv)


def test_build_argv_full() -> None:
    req = ClaudeRequest(
        prompt="hi",
        model="haiku",
        resume_session="sess-1",
        append_system_prompt="You are Jarvis.",
        mcp_config=Path("/etc/jarvis/mcp.json"),
        allowed_tools=("mcp__jarvis__memory_read", "WebSearch"),
    )
    argv = build_argv(req, "/bin/claude", Path("/tmp/sp.md"))
    assert "--model=haiku" in argv
    assert "--resume=sess-1" in argv
    assert "--append-system-prompt-file=/tmp/sp.md" in argv
    assert not any("You are Jarvis." in a for a in argv)
    assert "--mcp-config=/etc/jarvis/mcp.json" in argv
    assert "--strict-mcp-config" in argv
    assert "--allowedTools=mcp__jarvis__memory_read,WebSearch" in argv


def test_build_argv_never_skips_permissions() -> None:
    with pytest.raises(ValueError, match="permission"):
        ClaudeRequest(prompt="x", permission_mode="bypassPermissions")


@pytest.mark.parametrize("bad", ["--evil", "has space", "a,b"])
def test_rejects_suspicious_tool_names(bad: str) -> None:
    with pytest.raises(ValueError, match="tool"):
        ClaudeRequest(prompt="x", allowed_tools=(bad,))


def test_rejects_option_like_session_and_model() -> None:
    with pytest.raises(ValueError):
        ClaudeRequest(prompt="x", resume_session="--dangerously-skip-permissions")
    with pytest.raises(ValueError):
        ClaudeRequest(prompt="x", model="--x")


def test_parse_event_types() -> None:
    lines = (FIXTURES / "claude_stream_ok.jsonl").read_text().splitlines()
    events = [e for line in lines for e in parse_events(line)]
    kinds = [type(e) for e in events]
    assert kinds == [InitEvent, TextEvent, ToolUseEvent, RateLimitEvent, ResultEvent]
    result = events[-1]
    assert isinstance(result, ResultEvent)
    assert (result.text, result.session_id, result.is_error) == ("ok", "sess-fixture", False)
    assert result.cost_usd > 0 and result.tokens_out == 4
    limit = events[3]
    assert isinstance(limit, RateLimitEvent)
    assert limit.five_hour_utilization == pytest.approx(0.34)
    assert limit.seven_day_utilization == pytest.approx(0.06)
    assert limit.status == "allowed"


def test_parse_event_ignores_garbage_and_unknown() -> None:
    assert parse_events("not json") == []
    assert parse_events("[1, 2]") == []
    assert parse_events(json.dumps({"type": "system", "subtype": "hook_started"})) == []
    assert parse_events("") == []


def test_parse_assistant_message_with_several_blocks() -> None:
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Checking, sir."},
                    {"type": "tool_use", "id": "t", "name": "WebSearch", "input": {"q": "x"}},
                    {"type": "thinking", "thinking": "hmm"},
                ]
            },
        }
    )
    events = parse_events(line)
    assert [type(e) for e in events] == [TextEvent, ToolUseEvent]


async def test_run_streams_events_and_sends_prompt_on_stdin(fake: Path) -> None:
    events = [e async for e in run_claude(_req(claude_bin=_bin(), prompt="Hello Jarvis"))]
    assert isinstance(events[-1], ResultEvent)
    recorded = json.loads(fake.read_text())
    assert recorded["stdin"] == "Hello Jarvis"
    assert "Hello Jarvis" not in recorded["argv"]


async def test_run_to_result_collects_text_and_result(fake: Path) -> None:
    outcome = await run_to_result(_req(claude_bin=_bin()))
    assert outcome.result.text == "ok"
    assert outcome.rate_limit is not None
    assert [t.name for t in outcome.tool_uses] == ["mcp__jarvis__memory_search"]


async def test_nonzero_exit_without_result_raises(
    fake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FAKE_FIXTURE")
    monkeypatch.setenv("FAKE_EXIT", "1")
    monkeypatch.setenv("FAKE_STDERR", "not logged in")
    with pytest.raises(ClaudeError, match="exited with 1") as exc:
        await run_to_result(_req(claude_bin=_bin()))
    assert "not logged in" not in str(exc.value)
    assert exc.value.stderr == "not logged in"


async def test_missing_result_raises(fake: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FAKE_FIXTURE")
    with pytest.raises(ClaudeError, match="no result event"):
        await run_to_result(_req(claude_bin=_bin()))


async def test_timeout_kills_process(fake: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_SLEEP", "5")
    with pytest.raises(ClaudeError, match="timed out"):
        await run_to_result(_req(claude_bin=_bin(), timeout_s=0.5))


async def test_missing_binary_raises(tmp_path: Path) -> None:
    with pytest.raises(ClaudeError, match="not found"):
        await run_to_result(_req(claude_bin=str(tmp_path / "nope")))


async def test_system_prompt_goes_through_private_file(fake: Path) -> None:
    await run_to_result(_req(claude_bin=_bin(), append_system_prompt="Persona: butler"))
    argv = json.loads(fake.read_text())["argv"]
    flag = next(a for a in argv if a.startswith("--append-system-prompt-file="))
    assert not any("butler" in a for a in argv)
    assert not Path(flag.split("=", 1)[1]).exists()  # cleaned up after the run


async def test_child_env_drops_secrets(fake: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis.brain.core.claude_runner import child_env

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("JARVIS_DEVICE_TOKEN", "tok")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth")
    env = child_env((("EXTRA", "1"),))
    assert "ANTHROPIC_API_KEY" not in env and "JARVIS_DEVICE_TOKEN" not in env
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth" and env["EXTRA"] == "1"


def test_rejects_nul_and_huge_system_prompt() -> None:
    with pytest.raises(ValueError):
        ClaudeRequest(prompt="x", append_system_prompt="a\x00b")
    with pytest.raises(ValueError):
        ClaudeRequest(prompt="x", append_system_prompt="a" * 300_000)
    with pytest.raises(ValueError):
        ClaudeRequest(prompt="a\x00b")


async def test_relative_binary_is_rejected() -> None:
    with pytest.raises(ClaudeError, match="not found"):
        await run_to_result(_req(claude_bin="claude-relative"))


async def test_consumer_closing_early_kills_child(
    fake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import contextlib
    import time

    monkeypatch.setenv("FAKE_SLEEP", "0")
    start = time.monotonic()
    async with contextlib.aclosing(run_claude(_req(claude_bin=_bin()))) as events:
        async for _ in events:
            break
    assert time.monotonic() - start < 5
