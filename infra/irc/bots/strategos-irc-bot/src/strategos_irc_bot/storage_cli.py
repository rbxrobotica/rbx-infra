"""Explicit offline PostgreSQL administration, never exposed over IRC or MCP."""

from __future__ import annotations

import argparse
from pathlib import Path
import time

from .agent_config import AgentConfig
from .mailbox_database import (
    ADVISORY_LOCK,
    TABLE_COLUMNS,
    Connection,
    initialize_postgres,
    postgres_connection,
)


def add_storage_parser(subparsers: argparse._SubParsersAction) -> None:
    storage = subparsers.add_parser("storage", help="offline PostgreSQL administration")
    commands = storage.add_subparsers(dest="storage_command", required=True)
    commands.add_parser(
        "initialize", help="initialize a dedicated empty schema explicitly"
    )
    commands.add_parser(
        "status", help="inspect metadata/counts without expiry or dispatch"
    )
    migration = commands.add_parser(
        "migrate-sqlite", help="import a frozen private snapshot"
    )
    migration.add_argument("--source", type=Path, required=True)
    migration.add_argument("--source-sha256", required=True)
    migration.add_argument("--confirm-quiesced", action="store_true", required=True)
    migration.add_argument("--attest-legacy-transport", action="store_true")
    release = commands.add_parser(
        "release-migration", help="release a reconciled import hold"
    )
    release.add_argument("--confirm-reconciled", action="store_true", required=True)


def _blockers(db: Connection) -> dict[str, int]:
    # No expiry, retries, cancellation or reconciliation is implicit in admin I/O.
    queries = {
        "peer_pending": "SELECT count(*) FROM requests WHERE status='pending'",
        "peer_unsent": "SELECT count(*) FROM outbox WHERE sent=0",
        "human_open_sessions": "SELECT count(*) FROM human_sessions WHERE closed_at IS NULL OR admission_open<>0",
        "human_unresolved": "SELECT count(*) FROM human_requests WHERE status IN ('received','in_progress','uncertain') OR delivery_status IN ('pending','output_uncertain')",
        "human_unresolved_frames": "SELECT count(*) FROM human_outbox WHERE status IN ('pending','output_uncertain')",
    }
    return {name: db.execute(sql).fetchone()[0] for name, sql in queries.items()}


def storage_status(config: AgentConfig) -> dict:
    with postgres_connection(config, allow_hold=True) as db:
        meta = dict(db.execute("SELECT key,value FROM meta").fetchall())
        return {
            "backend": "postgresql",
            "schema_version": meta["schema_version"],
            "migration_hold": meta.get("migration_hold"),
            "source_sha256": meta.get("migration_source_sha256"),
            "row_sha256": meta.get("migration_row_sha256"),
            "legacy_transport_attested": meta.get("migration_transport_attested")
            == "true",
            "released_at": meta.get("migration_released_at"),
            "counts": {
                name: db.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
                for name in TABLE_COLUMNS
            },
            "release_blockers": _blockers(db),
        }


def release_migration(config: AgentConfig, *, confirm_reconciled: bool = False) -> dict:
    if confirm_reconciled is not True:
        raise ValueError(
            "release requires explicit offline reconciliation confirmation"
        )
    with postgres_connection(config, allow_hold=True) as db:
        if not db.execute(
            "SELECT pg_try_advisory_xact_lock(?)", (ADVISORY_LOCK + 1,)
        ).fetchone()[0]:
            raise ValueError("stop the mailbox transport before releasing migration")
        hold = db.execute(
            "SELECT value FROM meta WHERE key='migration_hold'"
        ).fetchone()
        if not hold or hold[0] != "reconciliation_required":
            raise ValueError("no recognized migration hold to release")
        if any(_blockers(db).values()):
            raise ValueError(
                "migration has unresolved work; inspect storage status and reconcile offline"
            )
        db.execute("DELETE FROM meta WHERE key='migration_hold'")
        db.execute(
            "INSERT INTO meta(key,value) VALUES ('heartbeat','0') ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        )
        db.execute(
            "INSERT INTO meta(key,value) VALUES ('migration_released_at',?)",
            (str(int(time.time())),),
        )
    return {"migration_hold": None, "released": True, "transport_started": False}


def run_storage_command(config: AgentConfig, args: argparse.Namespace) -> dict:
    if config.storage_backend != "postgresql":
        raise ValueError("storage administration requires storage_backend=postgresql")
    if args.storage_command == "initialize":
        return initialize_postgres(config)
    if args.storage_command == "status":
        return storage_status(config)
    if args.storage_command == "migrate-sqlite":
        from .mailbox_migration import migrate_sqlite

        return migrate_sqlite(
            config,
            args.source,
            args.source_sha256,
            confirm_quiesced=args.confirm_quiesced,
            attest_legacy_transport=args.attest_legacy_transport,
        )
    if args.storage_command == "release-migration":
        return release_migration(config, confirm_reconciled=args.confirm_reconciled)
    raise ValueError("unknown storage command")
