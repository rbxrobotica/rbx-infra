"""Offline, all-or-nothing import of a private SQLite mailbox snapshot.

No live SQLite mailbox is opened and no transport or provider is invoked. Every
import retains the original state and installs an administrative hold. A hold
must be reconciled separately; migration never replays or discards pending work.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
from dataclasses import dataclass

from .agent_config import AgentConfig


class MigrationError(ValueError):
    """An operator-safe error; never contains a path, DSN, SQL or mailbox text."""


@dataclass(frozen=True)
class _Snapshot:
    rows: dict[str, tuple[tuple, ...]]
    digests: dict[str, str]
    checksum: str
    transport_attested: bool


def _digest(rows: tuple[tuple, ...]) -> str:
    """Order-independent, type-preserving digest, including nulls and floats."""
    encoded = []
    for row in rows:
        values = []
        for value in row:
            if value is None:
                values.append(["null"])
            elif type(value) is int:
                values.append(["int", str(value)])
            elif type(value) is float and math.isfinite(value):
                values.append(["float", value.hex()])
            elif type(value) is str:
                values.append(["text", value])
            else:
                raise MigrationError("snapshot contains unsupported field values")
        encoded.append(
            json.dumps(values, ensure_ascii=True, separators=(",", ":")).encode()
        )
    digest = hashlib.sha256()
    for row in sorted(encoded):
        digest.update(len(row).to_bytes(8, "big"))
        digest.update(row)
    return digest.hexdigest()


def _schema_sql(sql: str | None) -> tuple[str, ...] | None:
    if sql is None:
        return None
    # Whitespace/identifier quoting are immaterial; string literals, defaults,
    # constraints, collations and extra SQL remain part of the closed schema.
    tokens = re.findall(
        r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|[A-Za-z_][A-Za-z_0-9]*|\d+|[^\s]", sql
    )
    normalized = [
        token if token.startswith("'") else token.strip('"').casefold()
        for token in tokens
    ]
    if normalized[2:5] == ["if", "not", "exists"]:
        del normalized[2:5]
    return tuple(normalized)


def _schema(db: sqlite3.Connection) -> dict[tuple[str, str, str], tuple | None]:
    return {
        (kind, name, table): _schema_sql(sql)
        for kind, name, table, sql in db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master"
        )
    }


def _columns_sql(columns: tuple[str, ...]) -> str:
    # Names come exclusively from the closed backend allowlist.
    return ",".join(f'"{name}"' for name in columns)


def _validate_database(db: sqlite3.Connection, config: AgentConfig, attest: bool):
    from .mailbox_database import (
        HUMAN_SCHEMA,
        LEGACY_SCHEMA,
        SCHEMA_VERSION,
        TABLE_COLUMNS,
        mailbox_identity,
        transport_identity,
    )

    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA trusted_schema=OFF")
    if (
        db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
        or db.execute("PRAGMA application_id").fetchone()[0] != 0
        or db.execute("PRAGMA user_version").fetchone()[0] != 0
    ):
        raise MigrationError("snapshot integrity or schema version is invalid")
    found = _schema(db)
    tables = {name for kind, name, _ in found if kind == "table"}
    base = {"meta", "requests", "outbox", "fragments", "rate_limits"}
    humans = set(TABLE_COLUMNS) - base
    if tables not in (base, base | humans):
        raise MigrationError("snapshot has an unknown or incomplete schema")
    reference = sqlite3.connect(":memory:")
    try:
        for statement in LEGACY_SCHEMA + (HUMAN_SCHEMA if tables & humans else ()):
            reference.execute(statement)
        if found != _schema(reference):
            raise MigrationError("snapshot has an unknown or altered schema")
    finally:
        reference.close()

    rows = {}
    for table, columns in TABLE_COLUMNS.items():
        if table not in tables:
            continue
        rows[table] = tuple(
            tuple(row)
            for row in db.execute(f'SELECT {_columns_sql(columns)} FROM "{table}"')
        )
        # SQLite accepts dynamic types that PostgreSQL would silently coerce.
        # Reject them now; a successful copy must preserve every field exactly.
        specs = db.execute(f'PRAGMA table_info("{table}")').fetchall()
        for row in rows[table]:
            for value, spec in zip(row, specs, strict=True):
                _, _, kind, nonnull, _, primary = spec
                if value is None:
                    if nonnull or primary:
                        raise MigrationError("snapshot has invalid null fields")
                elif (
                    (kind == "TEXT" and type(value) is not str)
                    or (kind == "INTEGER" and type(value) is not int)
                    or (
                        kind == "REAL"
                        and (type(value) is not float or not math.isfinite(value))
                    )
                ):
                    raise MigrationError("snapshot has invalid field types")

    meta = dict(rows["meta"])
    if set(meta) - {
        "identity",
        "human_transport_identity",
        "schema_version",
        "heartbeat",
    }:
        raise MigrationError("snapshot contains unknown mailbox metadata")
    if meta.get("identity") != mailbox_identity(config):
        raise MigrationError("snapshot belongs to a different agent/account/channel")
    if "schema_version" in meta and meta["schema_version"] != SCHEMA_VERSION:
        raise MigrationError("snapshot mailbox version is unsupported")
    if "human_transport_identity" in meta:
        if meta["human_transport_identity"] != transport_identity(config):
            raise MigrationError("snapshot belongs to a different IRC transport")
        attested = False
    else:
        if tables & humans:
            raise MigrationError("human snapshot is missing its transport identity")
        if not attest:
            raise MigrationError(
                "legacy snapshot requires explicit transport attestation"
            )
        attested = True
    for row in rows.get("human_sessions", ()):
        if row[TABLE_COLUMNS["human_sessions"].index("channel")] != config.channel:
            raise MigrationError("snapshot human session has a different channel")
    return rows, attested


def _fingerprint(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_uid,
        stat.S_IMODE(info.st_mode),
        info.st_nlink,
    )


def _hash_fd(fd: int) -> str:
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while block := os.read(fd, 1024 * 1024):
        digest.update(block)
    return digest.hexdigest()


def _no_sidecars(parent_fd: int, name: str) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        try:
            os.stat(name + suffix, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        raise MigrationError("snapshot must not have SQLite journal or WAL sidecars")


def _read_snapshot(
    config: AgentConfig, source: Path, expected_sha256: str, *, attest: bool = False
) -> _Snapshot:
    if not isinstance(expected_sha256, str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", expected_sha256
    ):
        raise MigrationError(
            "source SHA-256 is required and must contain 64 hexadecimal characters"
        )
    expected_sha256 = expected_sha256.lower()
    source = Path(source).expanduser().absolute()
    if ".." in source.parts:
        raise MigrationError("snapshot path must not contain parent traversal")
    parent_fd = fd = None
    try:
        for component in (source, *source.parents):
            if stat.S_ISLNK(component.lstat().st_mode):
                raise MigrationError("snapshot path must not contain symlinks")
        if not stat.S_ISREG(source.lstat().st_mode):
            raise MigrationError("snapshot must be a regular file")
        if source == config.state_dir.absolute() / "mailbox.sqlite3":
            raise MigrationError(
                "source must be an offline snapshot, not the installed mailbox"
            )
        parent_fd = os.open(source.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        parent_info = os.fstat(parent_fd)
        if parent_info.st_uid != os.getuid() or parent_info.st_mode & 0o077:
            raise MigrationError("snapshot directory must be owned and private (0700)")
        _no_sidecars(parent_fd, source.name)
        fd = os.open(
            source.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent_fd,
        )
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            raise MigrationError(
                "snapshot must be an owned private regular file without hardlinks"
            )
        # Also reject an installed mailbox named via a different lexical path.
        try:
            installed = (config.state_dir / "mailbox.sqlite3").stat()
        except FileNotFoundError:
            installed = None
        if installed and (installed.st_dev, installed.st_ino) == (
            info.st_dev,
            info.st_ino,
        ):
            raise MigrationError(
                "source must be an offline snapshot, not the installed mailbox"
            )
        if _hash_fd(fd) != expected_sha256:
            raise MigrationError(
                "snapshot SHA-256 does not match the expected checksum"
            )
        # The already-open inode is used, avoiding a pathname swap before SQLite
        # opens it. immutable=1 forbids journal recovery, locking and writes.
        db = sqlite3.connect(f"file:/proc/self/fd/{fd}?mode=ro&immutable=1", uri=True)
        try:
            rows, attested = _validate_database(db, config, attest)
        finally:
            db.close()
        _no_sidecars(parent_fd, source.name)
        if (
            _fingerprint(os.fstat(fd)) != _fingerprint(info)
            or _fingerprint(
                os.stat(source.name, dir_fd=parent_fd, follow_symlinks=False)
            )
            != _fingerprint(info)
            or _hash_fd(fd) != expected_sha256
        ):
            raise MigrationError("snapshot changed while it was being validated")
        return _Snapshot(
            rows,
            {table: _digest(data) for table, data in rows.items()},
            expected_sha256,
            attested,
        )
    except MigrationError:
        raise
    except Exception:
        raise MigrationError("snapshot could not be validated safely") from None
    finally:
        if fd is not None:
            os.close(fd)
        if parent_fd is not None:
            os.close(parent_fd)


def migrate_sqlite(
    config: AgentConfig,
    source: Path,
    source_sha256: str,
    *,
    confirm_quiesced: bool = False,
    attest_legacy_transport: bool = False,
) -> dict:
    """Copy one frozen snapshot into an empty, explicitly initialized database.

    The caller must first stop every daemon, CLI and MCP writer. This function
    cannot prove that operational attestation. It does not initialize a target,
    overwrite records, auto-retry errors, or release the reconciliation hold.
    """
    from .mailbox_database import (
        ADVISORY_LOCK,
        SCHEMA_VERSION,
        TABLE_COLUMNS,
        mailbox_identity,
        postgres_connection,
        reset_postgres_sequences,
        transport_identity,
    )

    if getattr(config, "storage_backend", "sqlite") != "postgresql":
        raise MigrationError("migration requires storage_backend=postgresql")
    if confirm_quiesced is not True:
        raise MigrationError(
            "migration requires explicit confirmation that all writers are stopped"
        )
    if type(attest_legacy_transport) is not bool:
        raise MigrationError("legacy transport attestation must be a boolean")
    snapshot = _read_snapshot(
        config, source, source_sha256, attest=attest_legacy_transport
    )
    expected_meta = {
        "schema_version": SCHEMA_VERSION,
        "identity": mailbox_identity(config),
        "human_transport_identity": transport_identity(config),
    }
    combined = hashlib.sha256(
        json.dumps(snapshot.digests, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    commit_started = False
    try:
        # This context takes exactly the same transaction-scoped advisory lock
        # as the runtime, covering the empty check, copy, verification and hold.
        with postgres_connection(config, allow_hold=True) as db:
            if not db.execute(
                "SELECT pg_try_advisory_xact_lock(?)", (ADVISORY_LOCK + 1,)
            ).fetchone()[0]:
                raise MigrationError(
                    "target transport is active; stop all writers before migration"
                )
            if (
                dict(db.execute("SELECT key,value FROM meta").fetchall())
                != expected_meta
            ):
                raise MigrationError(
                    "target must contain only initialized mailbox identity metadata"
                )
            for table in TABLE_COLUMNS:
                if (
                    table != "meta"
                    and db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                ):
                    raise MigrationError(
                        "target mailbox must be empty; existing records will not be overwritten"
                    )
            for table, rows in snapshot.rows.items():
                columns = TABLE_COLUMNS[table]
                copied = rows
                if table == "meta":
                    copied = tuple(row for row in rows if row[0] not in expected_meta)
                    if any(
                        expected_meta.get(key, value) != value for key, value in rows
                    ):
                        raise MigrationError(
                            "source and target identity metadata differ"
                        )
                if copied:
                    db.executemany(
                        f'INSERT INTO "{table}" ({_columns_sql(columns)}) VALUES ({",".join("?" for _ in columns)})',
                        copied,
                    )
            for table, rows in snapshot.rows.items():
                copied = tuple(
                    tuple(row)
                    for row in db.execute(
                        f'SELECT {_columns_sql(TABLE_COLUMNS[table])} FROM "{table}"'
                    ).fetchall()
                )
                if table == "meta":
                    original_keys = {row[0] for row in rows}
                    copied = tuple(row for row in copied if row[0] in original_keys)
                if (
                    len(copied) != len(rows)
                    or _digest(copied) != snapshot.digests[table]
                ):
                    raise MigrationError(
                        "migration row count or canonical digest verification failed"
                    )
            reset_postgres_sequences(db)
            db.executemany(
                "INSERT INTO meta(key,value) VALUES (?,?)",
                (
                    ("migration_source_sha256", snapshot.checksum),
                    ("migration_row_sha256", combined),
                    (
                        "migration_transport_attested",
                        "true" if snapshot.transport_attested else "false",
                    ),
                    ("migration_hold", "reconciliation_required"),
                ),
            )
            commit_started = True
    except MigrationError:
        raise
    except Exception:
        if commit_started:
            raise MigrationError(
                "migration commit outcome is uncertain; inspect target status and checksum; do not retry"
            ) from None
        raise MigrationError(
            "migration failed; inspect target status before any further action; do not retry automatically"
        ) from None
    return {
        "source_sha256": snapshot.checksum,
        "row_sha256": combined,
        "counts": {table: len(rows) for table, rows in snapshot.rows.items()},
        "migration_hold": "reconciliation_required",
        "legacy_transport_attested": snapshot.transport_attested,
    }
