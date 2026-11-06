from pathlib import Path

import pytest
from pydantic import ValidationError

from jarvis.shared.config import Settings, load_settings


def test_defaults_are_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("JARVIS_BIND_HOSTS", "JARVIS_PORT", "JARVIS_LOG_LEVEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    settings = load_settings()
    assert settings.bind_hosts == ("127.0.0.1",)
    assert settings.port == 8765
    assert settings.data_dir == tmp_path


def test_reads_bind_hosts_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JARVIS_BIND_HOSTS", '["127.0.0.1", "100.101.102.103"]')
    assert load_settings().bind_hosts == ("127.0.0.1", "100.101.102.103")


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "not-an-ip", "8.8.8.8"])  # noqa: S104
def test_rejects_public_or_wildcard_hosts(tmp_path: Path, host: str) -> None:
    with pytest.raises(ValidationError):
        Settings(data_dir=tmp_path, bind_hosts=(host,))


def test_accepts_tailscale_cgnat_range(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, bind_hosts=("100.64.0.1", "::1"))
    assert settings.bind_hosts == ("100.64.0.1", "::1")


def test_rejects_empty_bind_hosts(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Settings(data_dir=tmp_path, bind_hosts=())


@pytest.mark.parametrize("port", [0, 80, 70000])
def test_rejects_bad_ports(tmp_path: Path, port: int) -> None:
    with pytest.raises(ValidationError):
        Settings(data_dir=tmp_path, port=port)


def test_settings_are_immutable(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    with pytest.raises(ValidationError):
        settings.port = 9999  # type: ignore[misc]


def test_db_path_lives_in_data_dir(tmp_path: Path) -> None:
    assert Settings(data_dir=tmp_path).db_path == tmp_path / "jarvis.db"


def test_rejects_unknown_log_level(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Settings(data_dir=tmp_path, log_level="LOUD")  # type: ignore[arg-type]
