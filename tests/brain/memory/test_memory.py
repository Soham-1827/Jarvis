import subprocess
from pathlib import Path

import pytest

from jarvis.brain.memory.memory import Memory
from jarvis.brain.store.db import open_database


@pytest.fixture
def memory(tmp_path: Path) -> Memory:
    return Memory.open(tmp_path / "mem", open_database(tmp_path / "j.db"))


def _log(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(root), "log", "--format=%s"], capture_output=True, text=True, check=True
    )
    return out.stdout.splitlines()


def test_write_is_readable_searchable_and_committed(memory: Memory) -> None:
    memory.write("areas/jarvis.md", "Jarvis runs on the M700 home server")
    assert memory.read("areas/jarvis.md").startswith("Jarvis")
    assert [h.path for h in memory.search("server")] == ["areas/jarvis.md"]
    assert _log(memory.root) == ["memory: write areas/jarvis.md"]


def test_forget_removes_everywhere(memory: Memory) -> None:
    memory.write("people/alex.md", "Alex, robotics lab partner")
    assert memory.forget("people/alex.md") is True
    assert memory.read("people/alex.md") is None
    assert memory.search("robotics") == []
    assert _log(memory.root)[0] == "memory: forget people/alex.md"
    assert memory.forget("people/alex.md") is False


def test_append_to_daily_log(memory: Memory) -> None:
    memory.append("log/2026-11-05.md", "- built the store")
    memory.append("log/2026-11-05.md", "- built memory")
    assert memory.read("log/2026-11-05.md") == "- built the store\n- built memory\n"
    assert [h.path for h in memory.search("memory")] == ["log/2026-11-05.md"]


def test_unchanged_write_makes_no_empty_commit(memory: Memory) -> None:
    memory.write("profile.md", "same")
    memory.write("profile.md", "same")
    assert len(_log(memory.root)) == 1


def test_reopen_rebuilds_index_from_files(tmp_path: Path) -> None:
    mem = Memory.open(tmp_path / "mem", open_database(tmp_path / "a.db"))
    mem.write("preferences.md", "short casual answers")
    fresh = Memory.open(tmp_path / "mem", open_database(tmp_path / "b.db"))
    assert [h.path for h in fresh.search("casual")] == ["preferences.md"]
    assert fresh.list() == ["preferences.md"]


def test_git_failure_does_not_lose_the_write(
    memory: Memory, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, "git")

    monkeypatch.setattr("jarvis.brain.memory.memory.subprocess.run", broken)
    memory.write("profile.md", "still saved")
    assert memory.read("profile.md") == "still saved"
    assert "memory commit failed" in caplog.text


def test_git_ignores_ambient_git_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    decoy = tmp_path / "decoy"
    monkeypatch.setenv("GIT_DIR", str(decoy))
    mem = Memory.open(tmp_path / "mem", open_database(tmp_path / "j.db"))
    mem.write("profile.md", "x")
    assert not decoy.exists()
    monkeypatch.delenv("GIT_DIR")
    assert _log(mem.root) == ["memory: write profile.md"]


def test_refuses_symlinked_git_dir(tmp_path: Path) -> None:
    from jarvis.brain.memory.memory import MemoryGitError

    root = tmp_path / "mem"
    root.mkdir(mode=0o700)
    (tmp_path / "elsewhere").mkdir()
    (root / ".git").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(MemoryGitError):
        Memory.open(root, open_database(tmp_path / "j.db"))
