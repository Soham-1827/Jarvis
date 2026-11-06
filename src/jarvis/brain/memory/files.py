"""Markdown memory files with a fixed, validated layout. No path outside it can be touched."""

from __future__ import annotations

import os
import re
import tempfile
from datetime import date
from pathlib import Path

MAX_FILE_BYTES = 64 * 1024
MAX_FILES = 500
MAX_TOTAL_BYTES = 8 * 1024 * 1024

# profile.md, preferences.md, areas/<slug>.md, people/<slug>.md, log/YYYY-MM-DD.md
_ALLOWED = re.compile(
    r"^(?:profile|preferences)\.md$"
    r"|^(?:areas|people)/[a-z0-9][a-z0-9-]{0,63}\.md$"
    r"|^log/(\d{4}-\d{2}-\d{2})\.md$",
    re.ASCII,
)


class MemoryPathError(ValueError):
    """A memory path outside the allowed layout."""


class MemoryLimitError(ValueError):
    """The memory store is full (file count or total size)."""


class MemoryPermissionError(RuntimeError):
    """The memory root is not private to this user."""


def _is_allowed(rel: str) -> bool:
    match = _ALLOWED.fullmatch(rel)
    if match is None:
        return False
    if match.group(1):  # log/YYYY-MM-DD.md must be a real date
        try:
            date.fromisoformat(match.group(1))
        except ValueError:
            return False
    return True


class MemoryFiles:
    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        stat = root.stat()
        if stat.st_mode & 0o077 or stat.st_uid != os.getuid():
            raise MemoryPermissionError(
                f"{root} must be owned by this user with mode 0700 (filesystem ignores chmod?)"
            )
        self.root = root.resolve()

    def _resolve(self, rel: str) -> Path:
        if not _is_allowed(rel):
            raise MemoryPathError(f"not a memory path: {rel!r}")
        path = (self.root / rel).resolve()
        if not path.is_relative_to(self.root):
            raise MemoryPathError(f"memory path escapes the store: {rel!r}")
        return path

    def read(self, rel: str) -> str | None:
        path = self._resolve(rel)
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None

    def write(self, rel: str, content: str) -> None:
        """Atomic, owner-only write."""
        path = self._resolve(rel)
        data = content.encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"memory file too large ({len(data)} > {MAX_FILE_BYTES} bytes)")
        self._check_capacity(path, len(data))
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            Path(tmp).replace(path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _check_capacity(self, path: Path, new_size: int) -> None:
        files = [self.root / rel for rel in self.list()]
        existing = path.stat().st_size if path.is_file() else 0
        if not path.is_file() and len(files) >= MAX_FILES:
            raise MemoryLimitError(f"memory holds the maximum of {MAX_FILES} files")
        total = sum(f.stat().st_size for f in files) - existing + new_size
        if total > MAX_TOTAL_BYTES:
            raise MemoryLimitError(f"memory would exceed {MAX_TOTAL_BYTES} bytes")

    def append(self, rel: str, line: str) -> None:
        existing = self.read(rel) or ""
        self.write(rel, f"{existing}{line.rstrip(chr(10))}\n")

    def delete(self, rel: str) -> bool:
        path = self._resolve(rel)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def list(self) -> list[str]:
        found = (
            p.relative_to(self.root).as_posix() for p in self.root.rglob("*.md") if p.is_file()
        )
        return sorted(rel for rel in found if _is_allowed(rel))
