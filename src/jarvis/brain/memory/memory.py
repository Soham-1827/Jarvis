"""Memory facade: every change updates the file, the search index and the memory git repo."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

from jarvis.brain.memory.files import MemoryFiles
from jarvis.brain.memory.index import Hit, MemoryIndex
from jarvis.brain.store.db import Database

log = logging.getLogger(__name__)

# Isolate git from planted or ambient config: no hooks, fsmonitor or signing prompts.
_GIT_OPTS = (
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.fsmonitor=false",
    "-c", "commit.gpgsign=false",
    "-c", "user.name=Jarvis",
    "-c", "user.email=jarvis@localhost",
)  # fmt: skip


def _git_env() -> dict[str, str]:
    """Environment without GIT_* overrides and without global/system git config."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
    return env


class MemoryGitError(RuntimeError):
    """The memory repo's .git is not a plain directory."""


class Memory:
    """Personal memory. Note: `forget` removes a file going forward; git history keeps old
    versions until the memory repo is purged (planned as an explicit, approval-gated action).
    """

    def __init__(self, files: MemoryFiles, index: MemoryIndex) -> None:
        self._files = files
        self._index = index
        self._lock = threading.Lock()

    @classmethod
    def open(cls, root: Path, db: Database) -> Memory:
        """Open (and if needed init) the memory repo, then rebuild the index from the files."""
        memory = cls(MemoryFiles(root), MemoryIndex(db))
        memory._git_init()
        memory.rebuild_index()
        return memory

    @property
    def root(self) -> Path:
        return self._files.root

    def read(self, path: str) -> str | None:
        return self._files.read(path)

    def list(self) -> list[str]:
        return self._files.list()

    def search(self, text: str, limit: int = 5) -> list[Hit]:
        return self._index.search(text, limit)

    def write(self, path: str, content: str) -> None:
        with self._lock:
            self._files.write(path, content)
            self._index.update(path, content)
            self._commit(path, f"memory: write {path}")

    def append(self, path: str, line: str) -> None:
        with self._lock:
            self._files.append(path, line)
            self._index.update(path, self._files.read(path) or "")
            self._commit(path, f"memory: append {path}")

    def forget(self, path: str) -> bool:
        with self._lock:
            if not self._files.delete(path):
                return False
            self._index.remove(path)
            self._commit(path, f"memory: forget {path}")
            return True

    def rebuild_index(self) -> None:
        self._index.rebuild({p: self._files.read(p) or "" for p in self._files.list()})

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        git = shutil.which("git") or "git"
        return subprocess.run(  # noqa: S603 - fixed argv, validated paths, no shell
            [git, *_GIT_OPTS, "-C", str(self.root), *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
            env=_git_env(),
        )

    def _git_init(self) -> None:
        dot_git = self.root / ".git"
        if dot_git.is_symlink() or (dot_git.exists() and not dot_git.is_dir()):
            raise MemoryGitError(f"{dot_git} must be a plain directory")
        if not dot_git.exists():
            self._git("init", "--quiet", "--initial-branch=main")

    def _commit(self, path: str, message: str) -> None:
        """Version the change. A git failure is logged, never fatal: the file is already saved."""
        try:
            self._git("add", "--all", "--", path)
            if self._git("status", "--porcelain", "--", path).stdout.strip():
                self._git("commit", "--quiet", "-m", message, "--", path)
        except (subprocess.SubprocessError, OSError):
            log.exception("memory commit failed for %s", path)
