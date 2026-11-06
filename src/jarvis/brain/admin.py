"""`jarvis-admin`: pair, list and revoke devices. Run on the server as the jarvis user."""

from __future__ import annotations

import argparse
import sys

from jarvis.brain.store.db import open_database
from jarvis.brain.store.devices import DeviceRepo
from jarvis.brain.store.repos import AuditRepo
from jarvis.shared.config import load_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jarvis-admin", description="Manage Jarvis devices")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("add", help="pair a device and print its token once").add_argument("name")
    sub.add_parser("list", help="list paired devices")
    sub.add_parser("revoke", help="revoke a device").add_argument("name")
    args = parser.parse_args(argv)

    db = open_database(load_settings().db_path)
    devices = DeviceRepo(db)
    audit = AuditRepo(db)
    try:
        if args.cmd == "add":
            try:
                device, token = devices.add(args.name)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)  # noqa: T201
                return 1
            audit.record("admin", "device_add", device.name)
            print(f"Paired {device.name}. Token (shown once, store it safely):\n{token}")  # noqa: T201
        elif args.cmd == "list":
            for d in devices.list():
                state = "revoked" if d.revoked_at else "active"
                print(f"{d.name:20} {state:8} last seen {d.last_seen or 'never'}")  # noqa: T201
        elif not devices.revoke(args.name):
            print(f"error: no active device named {args.name!r}", file=sys.stderr)  # noqa: T201
            return 1
        else:
            audit.record("admin", "device_revoke", args.name)
            print(f"Revoked {args.name}.")  # noqa: T201
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
