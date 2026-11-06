from pathlib import Path

import pytest

from jarvis.brain.memory.index import MemoryIndex, build_match_query
from jarvis.brain.store.db import open_database


@pytest.fixture
def index(tmp_path: Path) -> MemoryIndex:
    return MemoryIndex(open_database(tmp_path / "j.db"))


def test_search_finds_updated_documents(index: MemoryIndex) -> None:
    index.update("areas/jarvis.md", "Jarvis is a personal assistant running on the M700")
    index.update("people/alex.md", "Alex is my lab partner for robotics")
    hits = index.search("assistant")
    assert [h.path for h in hits] == ["areas/jarvis.md"]
    assert "assistant" in hits[0].snippet


def test_update_replaces_previous_content(index: MemoryIndex) -> None:
    index.update("profile.md", "likes python")
    index.update("profile.md", "likes rust")
    assert index.search("python") == []
    assert [h.path for h in index.search("rust")] == ["profile.md"]


def test_remove(index: MemoryIndex) -> None:
    index.update("people/alex.md", "Alex robotics")
    index.remove("people/alex.md")
    assert index.search("robotics") == []


def test_rebuild_replaces_everything(index: MemoryIndex) -> None:
    index.update("people/old.md", "stale entry")
    index.rebuild({"profile.md": "fresh content"})
    assert index.search("stale") == []
    assert [h.path for h in index.search("fresh")] == ["profile.md"]


@pytest.mark.parametrize(
    "query", ['"', "AND OR NOT", "a*b(c", "NEAR(", "col:value", "-", "'; DROP TABLE x; --"]
)
def test_hostile_queries_never_raise(index: MemoryIndex, query: str) -> None:
    index.update("profile.md", "some text")
    index.search(query)  # must not raise


def test_empty_query_returns_nothing(index: MemoryIndex) -> None:
    index.update("profile.md", "text")
    assert index.search("   ") == []


def test_match_query_quotes_terms() -> None:
    assert build_match_query('robot "arm"') == '"robot" OR "arm"'
    assert build_match_query("") == ""


def test_search_respects_limit(index: MemoryIndex) -> None:
    for i in range(5):
        index.update(f"areas/p{i}.md", "shared keyword")
    assert len(index.search("keyword", limit=3)) == 3


def test_huge_query_is_bounded() -> None:
    from jarvis.brain.memory.index import MAX_TERMS

    query = build_match_query("x" * 100_000 + " word")
    assert query.count('"') == 2 * MAX_TERMS
    assert len(query) <= MAX_TERMS * (64 + 6)
