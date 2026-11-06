from pathlib import Path

from fastapi.testclient import TestClient

from jarvis.brain.app import create_app
from jarvis.brain.services import Services
from jarvis.brain.store.db import open_database
from jarvis.brain.store.devices import DeviceRepo
from jarvis.brain.store.repos import AuditRepo
from jarvis.shared.config import Settings


def _client(tmp_path: Path) -> TestClient:
    db = open_database(tmp_path / "j.db")
    services = Services(db=db, devices=DeviceRepo(db), audit=AuditRepo(db), orchestrator=None)  # type: ignore[arg-type]
    return TestClient(create_app(Settings(data_dir=tmp_path), services=services))


def test_serves_pwa_shell(tmp_path: Path) -> None:
    client = _client(tmp_path)
    page = client.get("/")
    assert page.status_code == 200 and "<title>Jarvis</title>" in page.text
    for path in ("/app.js", "/app.css", "/sw.js", "/manifest.webmanifest", "/icon-192.png"):
        assert client.get(path).status_code == 200, path


def test_security_headers_on_every_response(tmp_path: Path) -> None:
    client = _client(tmp_path)
    for path in ("/", "/app.js", "/health"):
        headers = client.get(path).headers
        csp = headers["content-security-policy"]
        assert "default-src 'self'" in csp
        assert "script-src 'self'" in csp and "unsafe-inline" not in csp.split("style-src")[0]
        assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert headers["referrer-policy"] == "no-referrer"
        assert "microphone=(self)" in headers["permissions-policy"]


def test_no_inline_script_or_style_in_shell() -> None:
    html = (Path(__file__).parents[2] / "web" / "index.html").read_text()
    assert "<script>" not in html and "<style" not in html and "style=" not in html
    assert "onclick" not in html.lower()


def test_pwa_never_uses_innerhtml() -> None:
    js = (Path(__file__).parents[2] / "web" / "app.js").read_text()
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "eval(" not in js


def test_service_worker_scope_and_manifest_types(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert (
        client.get("/manifest.webmanifest")
        .headers["content-type"]
        .startswith("application/manifest+json")
    )
    assert client.get("/sw.js").headers["cache-control"] == "no-cache"


def test_unknown_paths_are_404_not_directory_listings(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/../pyproject.toml").status_code == 404
    assert client.get("/nope.txt").status_code == 404
