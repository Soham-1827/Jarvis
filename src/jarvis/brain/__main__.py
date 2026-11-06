"""`jarvis-brain` entry point: serve the brain on loopback and Tailscale addresses only."""

from __future__ import annotations

import contextlib
import logging
import socket
import sys

import uvicorn
from fastapi import FastAPI
from pydantic import ValidationError

from jarvis.brain.app import create_app
from jarvis.shared.config import load_settings

log = logging.getLogger("jarvis.brain")


def _bind(host: str, port: int) -> socket.socket:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
    except OSError:
        sock.close()
        raise
    return sock


def bind_all(hosts: tuple[str, ...], port: int) -> list[socket.socket]:
    """Bind one socket per host. On any failure, close what was opened and re-raise."""
    sockets: list[socket.socket] = []
    try:
        for host in hosts:
            sockets.append(_bind(host, port))
    except OSError:
        close_all(sockets)
        raise
    return sockets


def close_all(sockets: list[socket.socket]) -> None:
    for sock in sockets:
        with contextlib.suppress(OSError):
            sock.close()


def build_server(app: FastAPI, log_level: str) -> uvicorn.Server:
    return uvicorn.Server(uvicorn.Config(app, log_level=log_level.lower(), proxy_headers=False))


def serve(app: FastAPI, hosts: tuple[str, ...], port: int, log_level: str) -> None:
    """Run uvicorn on one socket per host (uvicorn alone binds a single host)."""
    sockets = bind_all(hosts, port)
    try:
        build_server(app, log_level).run(sockets=sockets)
    finally:
        close_all(sockets)


def main() -> None:
    try:
        settings = load_settings()
    except ValidationError as exc:
        print(f"jarvis-brain: invalid configuration\n{exc}", file=sys.stderr)  # noqa: T201
        raise SystemExit(2) from exc
    logging.basicConfig(level=settings.log_level)
    log.info("starting brain on %s port %d", ", ".join(settings.bind_hosts), settings.port)
    try:
        serve(create_app(settings), settings.bind_hosts, settings.port, settings.log_level)
    except OSError as exc:
        log.error("could not bind %s:%d: %s", settings.bind_hosts, settings.port, exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
