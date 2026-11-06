from dataclasses import dataclass, field
from pathlib import Path

import pytest

from jarvis.brain.core.claude_runner import (
    ClaudeError,
    ClaudeRequest,
    RateLimitEvent,
    ResultEvent,
    RunOutcome,
)
from jarvis.brain.core.orchestrator import ORCHESTRATOR_TOOLS, Orchestrator
from jarvis.brain.memory.memory import Memory
from jarvis.brain.store.db import open_database
from jarvis.brain.store.repos import AuditRepo, ConversationRepo, MessageRepo


def _outcome(text: str = "Good evening, sir.", session: str = "sess-1") -> RunOutcome:
    result = ResultEvent(False, text, session, "success", 0.01, 100, 20, 1)
    return RunOutcome(result, (text,), (), RateLimitEvent("allowed", 0.3, 0.1, 123), "haiku")


@dataclass
class FakeRunner:
    outcomes: list[RunOutcome | Exception] = field(default_factory=list)
    requests: list[ClaudeRequest] = field(default_factory=list)

    async def __call__(self, req: ClaudeRequest) -> RunOutcome:
        self.requests.append(req)
        nxt = self.outcomes.pop(0) if self.outcomes else _outcome()
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


@pytest.fixture
def setup(tmp_path: Path):
    db = open_database(tmp_path / "j.db")
    memory = Memory.open(tmp_path / "mem", db)
    runner = FakeRunner()
    orch = Orchestrator(db=db, memory=memory, runner=runner, mcp_config=tmp_path / "mcp.json")
    return orch, runner, db, memory


async def test_first_turn_creates_conversation_and_stores_messages(setup) -> None:
    orch, runner, db, _ = setup
    turn = await orch.handle_turn("Hello Jarvis", channel="phone")
    assert turn.reply == "Good evening, sir."
    assert turn.ok
    convo = ConversationRepo(db).get(turn.conversation_id)
    assert convo is not None and convo.claude_session_id == "sess-1"
    msgs = MessageRepo(db).list(turn.conversation_id)
    assert [(m.role, m.content) for m in msgs] == [
        ("user", "Hello Jarvis"),
        ("assistant", "Good evening, sir."),
    ]
    assert msgs[1].cost_usd == pytest.approx(0.01) and msgs[1].tokens_out == 20
    req = runner.requests[0]
    assert req.prompt == "Hello Jarvis"
    assert req.resume_session is None
    assert req.allowed_tools == ORCHESTRATOR_TOOLS
    assert req.mcp_config == orch.mcp_config
    assert req.permission_mode == "default"


async def test_follow_up_resumes_session(setup) -> None:
    orch, runner, _, _ = setup
    first = await orch.handle_turn("Hi", channel="terminal")
    await orch.handle_turn("And again", channel="terminal", conversation_id=first.conversation_id)
    assert runner.requests[1].resume_session == "sess-1"


async def test_system_prompt_carries_profile_and_relevant_memory(setup) -> None:
    orch, runner, _, memory = setup
    memory.write("profile.md", "Soham studies computer science")
    memory.write("areas/robotics.md", "Robotics club meets Thursdays")
    await orch.handle_turn("When is robotics?", channel="phone")
    system = runner.requests[0].append_system_prompt or ""
    assert "Soham studies computer science" in system
    assert "areas/robotics.md" in system


async def test_failed_resume_retries_fresh_once(setup) -> None:
    orch, runner, _, _ = setup
    first = await orch.handle_turn("Hi", channel="terminal")
    runner.outcomes = [ClaudeError("claude exited with 1"), _outcome("Fresh start, sir.", "sess-2")]
    turn = await orch.handle_turn(
        "Hi again", channel="terminal", conversation_id=first.conversation_id
    )
    assert turn.reply == "Fresh start, sir."
    assert runner.requests[1].resume_session == "sess-1"
    assert runner.requests[2].resume_session is None


async def test_claude_failure_gives_polite_reply_and_audit(setup) -> None:
    orch, runner, db, _ = setup
    runner.outcomes = [ClaudeError("claude timed out after 300s")]
    turn = await orch.handle_turn("Hello", channel="phone")
    assert not turn.ok
    assert "afraid" in turn.reply.lower()
    assert "timed out" not in turn.reply  # internals stay out of the reply
    actions = [e.action for e in AuditRepo(db).recent()]
    assert "turn_failed" in actions


async def test_error_result_is_not_ok(setup) -> None:
    orch, runner, _, _ = setup
    bad = ResultEvent(True, "Prompt is too long", "sess-9", "error_during_execution", 0, 0, 0, 1)
    runner.outcomes = [RunOutcome(bad)]
    turn = await orch.handle_turn("Hi", channel="phone")
    assert not turn.ok


async def test_unknown_conversation_is_rejected(setup) -> None:
    orch, _, _, _ = setup
    with pytest.raises(LookupError):
        await orch.handle_turn("Hi", channel="phone", conversation_id="nope")


async def test_rate_limit_is_reported(setup) -> None:
    orch, _, _, _ = setup
    turn = await orch.handle_turn("Hi", channel="phone")
    assert turn.five_hour_utilization == pytest.approx(0.3)
    assert orch.last_rate_limit is not None


async def test_empty_message_is_rejected(setup) -> None:
    orch, _, _, _ = setup
    with pytest.raises(ValueError):
        await orch.handle_turn("   ", channel="phone")
