from pathlib import Path

import pytest

from jarvis.brain.store.db import open_database
from jarvis.brain.store.devices import DeviceRepo


@pytest.fixture
def repo(tmp_path: Path) -> DeviceRepo:
    return DeviceRepo(open_database(tmp_path / "j.db"))


def test_add_returns_token_once_and_stores_only_hash(repo: DeviceRepo, tmp_path: Path) -> None:
    device, token = repo.add("iphone")
    assert len(token) >= 40
    verified = repo.verify(token)
    assert verified is not None and verified.id == device.id
    raw = (tmp_path / "j.db").read_bytes()
    assert token.encode() not in raw


def test_verify_rejects_unknown_and_empty(repo: DeviceRepo) -> None:
    repo.add("iphone")
    assert repo.verify("wrong-token") is None
    assert repo.verify("") is None


def test_verify_updates_last_seen(repo: DeviceRepo) -> None:
    _, token = repo.add("iphone")
    seen = repo.verify(token)
    assert seen is not None and seen.last_seen is not None


def test_revoke_blocks_token(repo: DeviceRepo) -> None:
    _, token = repo.add("iphone")
    assert repo.revoke("iphone") is True
    assert repo.verify(token) is None
    assert repo.revoke("iphone") is False


def test_names_are_unique_and_validated(repo: DeviceRepo) -> None:
    repo.add("iphone")
    with pytest.raises(ValueError, match="exists"):
        repo.add("iphone")
    with pytest.raises(ValueError, match="name"):
        repo.add("bad name!")


def test_list_hides_hashes(repo: DeviceRepo) -> None:
    repo.add("iphone")
    repo.add("laptop")
    devices = repo.list()
    assert [d.name for d in devices] == ["iphone", "laptop"]
    assert not hasattr(devices[0], "token_hash")
