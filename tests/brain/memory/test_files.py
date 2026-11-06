from pathlib import Path

import pytest

from jarvis.brain.memory.files import MAX_FILE_BYTES, MemoryFiles, MemoryPathError


@pytest.fixture
def files(tmp_path: Path) -> MemoryFiles:
    return MemoryFiles(tmp_path / "mem")


@pytest.mark.parametrize(
    "path",
    [
        "profile.md",
        "preferences.md",
        "areas/jarvis.md",
        "people/prof-smith.md",
        "log/2026-11-05.md",
    ],
)
def test_write_read_roundtrip_for_allowed_paths(files: MemoryFiles, path: str) -> None:
    files.write(path, "# hello\n")
    assert files.read(path) == "# hello\n"
    assert path in files.list()


@pytest.mark.parametrize(
    "path",
    [
        "../etc/passwd",
        "/etc/passwd",
        "areas/../../x.md",
        "areas/Jarvis.md",
        "areas/a b.md",
        "secrets.md",
        "areas/x.txt",
        "log/2026-13-45.md",
        "people/.hidden.md",
        "areas/sub/x.md",
        "",
    ],
)
def test_rejects_paths_outside_layout(files: MemoryFiles, path: str) -> None:
    with pytest.raises(MemoryPathError):
        files.write(path, "x")
    with pytest.raises(MemoryPathError):
        files.read(path)


def test_read_missing_returns_none(files: MemoryFiles) -> None:
    assert files.read("profile.md") is None


def test_append_creates_then_appends(files: MemoryFiles) -> None:
    files.append("log/2026-11-05.md", "- did a thing")
    files.append("log/2026-11-05.md", "- did another")
    assert files.read("log/2026-11-05.md") == "- did a thing\n- did another\n"


def test_delete(files: MemoryFiles) -> None:
    files.write("people/alex.md", "Alex")
    assert files.delete("people/alex.md") is True
    assert files.read("people/alex.md") is None
    assert files.delete("people/alex.md") is False


def test_rejects_oversized_content(files: MemoryFiles) -> None:
    with pytest.raises(ValueError, match="too large"):
        files.write("profile.md", "x" * (MAX_FILE_BYTES + 1))


def test_files_are_private(files: MemoryFiles) -> None:
    files.write("areas/jarvis.md", "x")
    assert (files.root / "areas" / "jarvis.md").stat().st_mode & 0o077 == 0
    assert files.root.stat().st_mode & 0o077 == 0


def test_list_ignores_foreign_files(files: MemoryFiles) -> None:
    files.write("profile.md", "x")
    (files.root / "notes.txt").write_text("stray")
    (files.root / ".git").mkdir()
    assert files.list() == ["profile.md"]


def test_rejects_symlink_escape(files: MemoryFiles, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (files.root / "areas").symlink_to(outside, target_is_directory=True)
    with pytest.raises(MemoryPathError):
        files.write("areas/evil.md", "x")


@pytest.mark.parametrize("path", ["log/2026-02-31.md", "log/٢٠٢٦-01-01.md"])
def test_rejects_impossible_or_non_ascii_dates(files: MemoryFiles, path: str) -> None:
    with pytest.raises(MemoryPathError):
        files.write(path, "x")


def test_caps_file_count(files: MemoryFiles, monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis.brain.memory import files as files_mod

    monkeypatch.setattr(files_mod, "MAX_FILES", 2)
    files.write("areas/a.md", "x")
    files.write("areas/b.md", "x")
    files.write("areas/a.md", "rewrite is fine")
    with pytest.raises(files_mod.MemoryLimitError):
        files.write("areas/c.md", "x")


def test_caps_total_bytes(files: MemoryFiles, monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis.brain.memory import files as files_mod

    monkeypatch.setattr(files_mod, "MAX_TOTAL_BYTES", 10)
    files.write("areas/a.md", "12345")
    with pytest.raises(files_mod.MemoryLimitError):
        files.write("areas/b.md", "123456")


def test_refuses_non_private_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis.brain.memory import files as files_mod

    monkeypatch.setattr(files_mod.Path, "chmod", lambda self, mode: None)
    root = tmp_path / "shared"
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    with pytest.raises(files_mod.MemoryPermissionError):
        MemoryFiles(root)


def test_non_utf8_file_is_readable(files: MemoryFiles) -> None:
    files.write("profile.md", "ok")
    (files.root / "profile.md").write_bytes(b"caf\xe9")
    assert files.read("profile.md") == "caf�"


def test_write_cleans_temp_file_on_failure(
    files: MemoryFiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.brain.memory import files as files_mod

    def boom(self, target):
        raise OSError("disk full")

    monkeypatch.setattr(files_mod.Path, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        files.write("profile.md", "x")
    assert not list(files.root.glob(".tmp-*"))
