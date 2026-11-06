from pathlib import Path

import pytest

from jarvis.brain.admin import main


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    return tmp_path


def test_add_list_revoke(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["add", "iphone"]) == 0
    token = capsys.readouterr().out.strip().splitlines()[-1]
    assert len(token) >= 40
    assert main(["list"]) == 0
    assert "iphone" in capsys.readouterr().out
    assert main(["revoke", "iphone"]) == 0
    assert main(["revoke", "iphone"]) == 1
    assert main(["list"]) == 0
    assert "revoked" in capsys.readouterr().out


def test_duplicate_and_bad_names_fail(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["add", "iphone"]) == 0
    assert main(["add", "iphone"]) == 1
    assert main(["add", "Bad Name"]) == 1
    assert "error" in capsys.readouterr().err


def test_admin_actions_are_audited(data_dir: Path) -> None:
    from jarvis.brain.store.db import open_database
    from jarvis.brain.store.repos import AuditRepo

    main(["add", "iphone"])
    main(["revoke", "iphone"])
    actions = [e.action for e in AuditRepo(open_database(data_dir / "jarvis.db")).recent()]
    assert actions == ["device_revoke", "device_add"]
