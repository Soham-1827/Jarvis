from pathlib import Path

from fastapi.testclient import TestClient

from jarvis import __version__
from jarvis.brain.app import create_app
from jarvis.shared.config import Settings


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(Settings(data_dir=tmp_path)))


def test_health_reports_ok_and_version(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["uptime_s"] >= 0


def test_health_does_not_leak_config(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/health").json()
    assert set(body) == {"status", "version", "uptime_s"}


def test_docs_are_disabled(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_create_app_makes_data_dir(tmp_path: Path) -> None:
    data_dir = tmp_path / "nested" / "data"
    create_app(Settings(data_dir=data_dir))
    assert data_dir.is_dir()
    assert data_dir.stat().st_mode & 0o077 == 0
