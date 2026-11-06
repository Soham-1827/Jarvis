import socket
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from jarvis.brain import __main__ as brain_main
from jarvis.brain.app import create_app
from jarvis.shared.config import Settings


def test_main_serves_on_every_bind_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JARVIS_BIND_HOSTS", '["127.0.0.1", "100.64.0.7"]')
    monkeypatch.setenv("JARVIS_PORT", "9001")
    monkeypatch.setenv("JARVIS_LOG_LEVEL", "DEBUG")
    serve = MagicMock()
    monkeypatch.setattr(brain_main, "serve", serve)

    brain_main.main()

    _, hosts, port, level = serve.call_args.args
    assert (hosts, port, level) == (("127.0.0.1", "100.64.0.7"), 9001, "DEBUG")


def test_main_exits_2_on_bad_config(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("JARVIS_BIND_HOSTS", '["0.0.0.0"]')
    monkeypatch.setattr(brain_main, "serve", MagicMock())
    with pytest.raises(SystemExit) as exc:
        brain_main.main()
    assert exc.value.code == 2
    assert "invalid configuration" in capsys.readouterr().err


def test_main_exits_1_when_bind_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(brain_main, "serve", MagicMock(side_effect=OSError("in use")))
    with pytest.raises(SystemExit) as exc:
        brain_main.main()
    assert exc.value.code == 1


def test_bind_all_closes_opened_sockets_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[socket.socket] = []
    real_bind = brain_main._bind

    def flaky_bind(host: str, port: int) -> socket.socket:
        if host == "100.64.0.9":
            raise OSError("address not available")
        sock = real_bind(host, port)
        opened.append(sock)
        return sock

    monkeypatch.setattr(brain_main, "_bind", flaky_bind)
    with pytest.raises(OSError):
        brain_main.bind_all(("127.0.0.1", "100.64.0.9"), 0)
    assert opened and all(s.fileno() == -1 for s in opened)


def test_bind_closes_socket_when_bind_fails() -> None:
    with pytest.raises(OSError):
        brain_main._bind("100.64.0.9", 0)  # not assigned to this machine


def test_bind_ipv6_loopback() -> None:
    if not socket.has_ipv6:
        pytest.skip("no IPv6")
    try:
        sock = brain_main._bind("::1", 0)
    except OSError:
        pytest.skip("IPv6 loopback unavailable")
    assert sock.family == socket.AF_INET6
    sock.close()


def test_server_answers_health_on_bound_socket(tmp_path: Path) -> None:
    sockets = brain_main.bind_all(("127.0.0.1",), 0)
    port = sockets[0].getsockname()[1]
    server = brain_main.build_server(create_app(Settings(data_dir=tmp_path)), "WARNING")
    thread = threading.Thread(target=server.run, kwargs={"sockets": sockets}, daemon=True)
    thread.start()
    try:
        for _ in range(50):
            if server.started:
                break
            time.sleep(0.05)
        response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2)
        assert response.json()["status"] == "ok"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        brain_main.close_all(sockets)
