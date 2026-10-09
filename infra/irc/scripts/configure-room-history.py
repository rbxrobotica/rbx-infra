#!/usr/bin/env python3
"""Prepare private Ergo history configuration without deploying or printing secrets."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import yaml


def validate_catalog(catalog: dict) -> None:
    if not isinstance(catalog, dict):
        raise TypeError("expected a room catalog object")
    if catalog.get("schema") != "rbx-irc-rooms/v1" or catalog.get("tenant") != "rbx":
        raise ValueError("expected the single-tenant RBX room catalog")
    retention = catalog.get("retention_days")
    if type(retention) is not int or not 1 <= retention <= 90:
        raise ValueError("retention_days must be between 1 and 90")
    rooms = catalog.get("rooms")
    if not isinstance(rooms, list) or not 1 <= len(rooms) <= 50:
        raise ValueError("expected between 1 and 50 rooms")
    names = set()
    for room in rooms:
        if not isinstance(room, dict):
            raise TypeError("each room must be an object")
        channel = room.get("channel", "")
        if not re.fullmatch(r"#[a-z0-9][a-z0-9-]{1,62}", channel) or channel in names:
            raise ValueError("invalid or duplicate channel")
        names.add(channel)
        if not isinstance(room.get("members"), list):
            raise TypeError("each room requires an explicit account allowlist")
        for account in room["members"]:
            if not isinstance(account, str) or not re.fullmatch(
                r"[a-z0-9][a-z0-9-]{1,31}", account
            ):
                raise ValueError("invalid account in room allowlist")
        topic = room.get("topic", "")
        if (
            not isinstance(topic, str)
            or len(topic.encode()) > 280
            or any(ord(c) < 32 for c in topic)
        ):
            raise ValueError("invalid room topic")


def prepare(config: dict, catalog: dict) -> dict:
    validate_catalog(catalog)
    datastore = config.setdefault("datastore", {})
    for backend in ("mysql", "postgresql"):
        if datastore.get(backend, {}).get("enabled"):
            raise ValueError(
                "an existing persistent backend requires a reviewed migration"
            )
    sqlite = datastore.get("sqlite", {})
    if sqlite.get("enabled") and sqlite.get("database-path") != "ergo_history.db":
        raise ValueError("refusing to replace an existing SQLite history database")
    datastore["sqlite"] = {
        "enabled": True,
        "database-path": "ergo_history.db",
        "busy-timeout": "5s",
        "max-conns": 1,
    }
    history = config.setdefault("history", {})
    history["enabled"] = True
    history["client-length"] = 0
    history.setdefault("restrictions", {})["expire-time"] = (
        f"{catalog['retention_days']}d"
    )
    history["persistent"] = {
        "enabled": True,
        "unregistered-channels": False,
        "registered-channels": "opt-in",
        "direct-messages": "disabled",
    }
    return config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--rooms", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; inspect it before replacing")
    try:
        config = prepare(
            yaml.safe_load(args.config.read_text()), json.loads(args.rooms.read_text())
        )
    except (ValueError, TypeError, OSError, yaml.YAMLError):
        parser.error("unable to read validated configuration or room catalog")
    fd = os.open(
        args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(fd, "w") as stream:
        yaml.safe_dump(config, stream, sort_keys=False)
    print("Prepared private history configuration; no service changed.")


if __name__ == "__main__":
    main()
