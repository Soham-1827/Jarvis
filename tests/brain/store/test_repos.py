from pathlib import Path

import pytest

from jarvis.brain.store.db import Database, open_database
from jarvis.brain.store.repos import (
    ApprovalRepo,
    AuditRepo,
    ConversationRepo,
    JobRepo,
    MessageRepo,
)


@pytest.fixture
def conn(tmp_path: Path):
    db = open_database(tmp_path / "j.db")
    yield db
    db.close()


def test_conversation_create_get_and_set_session(conn) -> None:
    repo = ConversationRepo(conn)
    convo = repo.create(channel="phone")
    assert repo.get(convo.id) == convo
    assert convo.claude_session_id is None

    updated = repo.set_session(convo.id, "sess-123")
    assert updated.claude_session_id == "sess-123"
    assert convo.claude_session_id is None  # original object untouched
    assert repo.get(convo.id) == updated


def test_conversation_get_missing_returns_none(conn) -> None:
    assert ConversationRepo(conn).get("nope") is None


def test_conversation_rejects_unknown_channel(conn) -> None:
    with pytest.raises(ValueError, match="channel"):
        ConversationRepo(conn).create(channel="carrier-pigeon")


def test_messages_append_and_list_in_order(conn) -> None:
    convo = ConversationRepo(conn).create(channel="terminal")
    repo = MessageRepo(conn)
    repo.add(convo.id, "user", "Hello Jarvis")
    repo.add(
        convo.id, "assistant", "Good evening, sir.", cost_usd=0.002, tokens_in=10, tokens_out=5
    )
    msgs = repo.list(convo.id)
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[1].cost_usd == pytest.approx(0.002)
    assert msgs[1].tokens_out == 5


def test_messages_list_respects_limit_keeping_latest(conn) -> None:
    convo = ConversationRepo(conn).create(channel="terminal")
    repo = MessageRepo(conn)
    for i in range(5):
        repo.add(convo.id, "user", f"m{i}")
    assert [m.content for m in repo.list(convo.id, limit=2)] == ["m3", "m4"]


def test_message_requires_existing_conversation(conn) -> None:
    with pytest.raises(ValueError, match="conversation"):
        MessageRepo(conn).add("missing", "user", "hi")


def test_message_rejects_bad_role(conn) -> None:
    convo = ConversationRepo(conn).create(channel="terminal")
    with pytest.raises(ValueError, match="role"):
        MessageRepo(conn).add(convo.id, "robot", "hi")


def test_job_lifecycle(conn) -> None:
    repo = JobRepo(conn)
    job = repo.create(kind="code", task="fix the bug", repo="Jarvis")
    assert job.status == "queued"
    running = repo.set_status(job.id, "running")
    assert running.started_at is not None
    done = repo.set_status(job.id, "succeeded", result="all green")
    assert done.finished_at is not None and done.result == "all green"
    assert [j.id for j in repo.list(status="succeeded")] == [job.id]
    assert repo.list(status="running") == []


def test_job_rejects_unknown_status(conn) -> None:
    job = JobRepo(conn).create(kind="code", task="x")
    with pytest.raises(ValueError, match="status"):
        JobRepo(conn).set_status(job.id, "exploded")


def test_job_set_status_missing_raises(conn) -> None:
    with pytest.raises(LookupError):
        JobRepo(conn).set_status("nope", "running")


def test_approval_pending_then_decided_once(conn) -> None:
    repo = ApprovalRepo(conn)
    appr = repo.create(kind="git_push", summary="push to main", payload={"repo": "Jarvis"})
    assert [a.id for a in repo.pending()] == [appr.id]
    decided = repo.decide(appr.id, approved=True, decided_by="iphone")
    assert decided.status == "approved" and decided.payload == {"repo": "Jarvis"}
    assert repo.pending() == []
    with pytest.raises(ValueError, match="already"):
        repo.decide(appr.id, approved=False, decided_by="iphone")


def test_approval_decide_missing_raises(conn) -> None:
    with pytest.raises(LookupError):
        ApprovalRepo(conn).decide("nope", approved=True, decided_by="x")


def test_audit_records_and_lists_newest_first(conn) -> None:
    repo = AuditRepo(conn)
    repo.record(actor="orchestrator", action="memory_read", target="profile.md")
    repo.record(actor="iphone", action="approve", target="appr-1", detail={"ok": True})
    entries = repo.recent(limit=10)
    assert [e.action for e in entries] == ["approve", "memory_read"]
    assert entries[0].detail == {"ok": True}


def test_set_session_on_missing_conversation_raises(conn) -> None:
    with pytest.raises(LookupError):
        ConversationRepo(conn).set_session("nope", "s")


def test_job_list_all_newest_first(conn) -> None:
    repo = JobRepo(conn)
    first = repo.create(kind="code", task="a")
    second = repo.create(kind="code", task="b")
    ids = [j.id for j in repo.list()]
    assert set(ids) == {first.id, second.id}
    assert repo.get(first.id) == first
    assert repo.get("nope") is None


def test_approval_payload_is_copied_not_shared(conn) -> None:
    payload = {"repo": "Jarvis"}
    appr = ApprovalRepo(conn).create(kind="git_push", summary="push", payload=payload)
    payload["repo"] = "tampered"
    assert appr.payload == {"repo": "Jarvis"}


def test_final_job_status_cannot_change(conn) -> None:
    repo = JobRepo(conn)
    job = repo.create(kind="code", task="x")
    repo.set_status(job.id, "cancelled")
    with pytest.raises(ValueError, match="already cancelled"):
        repo.set_status(job.id, "running")


def test_job_result_kept_when_not_given(conn) -> None:
    repo = JobRepo(conn)
    job = repo.create(kind="code", task="x")
    repo.set_status(job.id, "waiting", result="partial")
    assert repo.set_status(job.id, "running").result == "partial"


def test_expired_approval_cannot_be_approved(conn) -> None:
    from datetime import timedelta

    repo = ApprovalRepo(conn)
    appr = repo.create(kind="git_push", summary="push", payload={}, ttl=timedelta(seconds=-1))
    with pytest.raises(ValueError, match="expired"):
        repo.decide(appr.id, approved=True, decided_by="iphone")
    assert repo.get(appr.id).status == "expired"


def test_pending_expires_stale(conn) -> None:
    from datetime import timedelta

    repo = ApprovalRepo(conn)
    repo.create(kind="send_email", summary="old", payload={}, ttl=timedelta(seconds=-1))
    live = repo.create(kind="send_email", summary="new", payload={})
    assert [a.id for a in repo.pending()] == [live.id]


def test_decide_requires_decider(conn) -> None:
    appr = ApprovalRepo(conn).create(kind="git_push", summary="push", payload={})
    with pytest.raises(ValueError, match="decided_by"):
        ApprovalRepo(conn).decide(appr.id, approved=True, decided_by="  ")


def test_concurrent_decisions_have_exactly_one_winner(conn) -> None:
    import threading

    repo = ApprovalRepo(conn)
    appr = repo.create(kind="git_push", summary="push", payload={})
    outcomes: list[str] = []
    barrier = threading.Barrier(8)

    def decide(i: int) -> None:
        barrier.wait()
        try:
            repo.decide(appr.id, approved=i % 2 == 0, decided_by=f"device-{i}")
            outcomes.append("won")
        except ValueError:
            outcomes.append("lost")

    threads = [threading.Thread(target=decide, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("won") == 1


def test_audit_log_is_append_only(conn) -> None:
    import sqlite3

    AuditRepo(conn).record(actor="x", action="y")
    with conn.tx() as c, pytest.raises(sqlite3.IntegrityError):
        c.execute("UPDATE audit SET action = 'z'")
    with conn.tx() as c, pytest.raises(sqlite3.IntegrityError):
        c.execute("DELETE FROM audit")
    assert len(AuditRepo(conn).recent()) == 1


@pytest.mark.parametrize("limit", [0, -1, 10_000])
def test_rejects_bad_limits(conn, limit: int) -> None:
    with pytest.raises(ValueError, match="limit"):
        JobRepo(conn).list(limit=limit)


def test_audit_empty_detail_roundtrips(conn) -> None:
    AuditRepo(conn).record(actor="x", action="y", detail={})
    assert AuditRepo(conn).recent()[0].detail == {}


def test_tx_rolls_back_on_error(conn: Database) -> None:
    with pytest.raises(RuntimeError), conn.tx() as c:
        c.execute("INSERT INTO audit (ts, actor, action) VALUES ('t', 'a', 'b')")
        raise RuntimeError("boom")
    assert AuditRepo(conn).recent() == []
