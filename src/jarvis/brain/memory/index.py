"""Full-text search over memory files (SQLite FTS5, table created by the store migrations)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from jarvis.brain.store.db import Database

_TERM = re.compile(r"\w{1,64}", re.UNICODE)
MAX_TERMS = 16
MAX_QUERY_CHARS = 2000


@dataclass(frozen=True, slots=True)
class Hit:
    path: str
    snippet: str
    score: float


def build_match_query(text: str) -> str:
    """Turn free text into a safe FTS5 query: each word quoted, OR-ed. No FTS syntax survives."""
    terms = _TERM.findall(text[:MAX_QUERY_CHARS])[:MAX_TERMS]
    return " OR ".join(f'"{term}"' for term in terms)


class MemoryIndex:
    def __init__(self, db: Database) -> None:
        self._db = db

    def update(self, path: str, content: str) -> None:
        with self._db.tx() as conn:
            conn.execute("DELETE FROM memory_fts WHERE path = ?", (path,))
            conn.execute("INSERT INTO memory_fts (path, content) VALUES (?, ?)", (path, content))

    def remove(self, path: str) -> None:
        with self._db.tx() as conn:
            conn.execute("DELETE FROM memory_fts WHERE path = ?", (path,))

    def rebuild(self, documents: Mapping[str, str]) -> None:
        with self._db.tx() as conn:
            conn.execute("DELETE FROM memory_fts")
            conn.executemany(
                "INSERT INTO memory_fts (path, content) VALUES (?, ?)", documents.items()
            )

    def search(self, text: str, limit: int = 5) -> list[Hit]:
        query = build_match_query(text)
        if not query:
            return []
        with self._db.read() as conn:
            rows = conn.execute(
                "SELECT path, snippet(memory_fts, 1, '', '', ' … ', 24) AS snippet,"
                " bm25(memory_fts) AS score FROM memory_fts WHERE memory_fts MATCH ?"
                " ORDER BY score LIMIT ?",
                (query, max(1, min(limit, 50))),
            ).fetchall()
        return [Hit(r["path"], r["snippet"], -r["score"]) for r in rows]
