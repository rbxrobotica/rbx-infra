"""Synthetic offline snapshots and disposable local PostgreSQL databases only."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import sqlite3

import pytest

from strategos_irc_bot.agent_config import AgentConfig
from strategos_irc_bot.agent_store import Mailbox
from strategos_irc_bot import mailbox_database as database
from strategos_irc_bot import mailbox_migration as migration


@pytest.fixture
def config(tmp_path):
    return AgentConfig(
        "local",
        "127.0.0.1:6667",
        False,
        "rbx-local",
        "rbx-local",
        "RBX_AGENT_IRC_PASSWORD",
        "#rbx-agents",
        {"thinkcentre": "rbx-thinkcentre"},
        frozenset({"leandro"}),
        tmp_path / "installed",
        human_queries=True,
    )


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(tmp_path, config, *, humans=True, transport=True):
    folder = tmp_path / "offline"
    folder.mkdir(mode=0o700)
    path = folder / "snapshot.sqlite3"
    with sqlite3.connect(path) as db:
        for statement in database.LEGACY_SCHEMA + (
            database.HUMAN_SCHEMA if humans else ()
        ):
            db.execute(statement)
        db.execute(
            "INSERT INTO meta VALUES ('identity', ?)",
            (database.mailbox_identity(config),),
        )
        if transport:
            db.execute(
                "INSERT INTO meta VALUES ('human_transport_identity', ?)",
                (database.transport_identity(config),),
            )
        db.executemany(
            "INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?)",
            (
                (
                    "peer-pending",
                    "out",
                    "rbx-thinkcentre",
                    "thinkcentre",
                    1000,
                    "Pergunta: café?",
                    "pending",
                    None,
                    0,
                ),
                (
                    "peer-replied",
                    "in",
                    "rbx-thinkcentre",
                    "thinkcentre",
                    990,
                    "input",
                    "replied",
                    "Resposta 🙂\nwith lines",
                    0,
                ),
                (
                    "peer-answered",
                    "out",
                    "rbx-thinkcentre",
                    "thinkcentre",
                    900,
                    "question",
                    "answered",
                    "cached answer",
                    1,
                ),
                (
                    "peer-expired",
                    "in",
                    "leandro",
                    "leandro",
                    800,
                    "older",
                    "expired",
                    None,
                    0,
                ),
            ),
        )
        db.executemany(
            "INSERT INTO outbox VALUES (?,?,?,?,?)",
            (
                (5, "peer-pending", "synthetic unsent frame", 0, 1001),
                (17, "peer-replied", "synthetic cached frame", 1, 999),
            ),
        )
        db.executemany(
            "INSERT INTO fragments VALUES (?,?,?,?)",
            (("peer-pending", 1, 3, "one "), ("peer-pending", 3, 3, "three")),
        )
        db.executemany(
            "INSERT INTO rate_limits VALUES (?,?,?)",
            (("leandro", 960, 4), ("rbx-thinkcentre", 1020, 6)),
        )
        if humans:
            db.executemany(
                "INSERT INTO human_meta VALUES (?,?)",
                (("reference", 123), ("fence", 456)),
            )
            db.executemany(
                "INSERT INTO human_sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    (
                        "session-open",
                        "flightdeck-dev",
                        "leandro",
                        config.channel,
                        "project",
                        "a" * 40,
                        "model",
                        999.25,
                        1999.75,
                        100,
                        6,
                        1,
                        "worker",
                        None,
                        None,
                    ),
                    (
                        "session-closed",
                        "review",
                        "leandro",
                        config.channel,
                        "project",
                        "b" * 40,
                        "model",
                        100.0,
                        700.0,
                        3,
                        2,
                        0,
                        None,
                        600.5,
                        "manual",
                    ),
                ),
            )
            db.executemany(
                "INSERT INTO human_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    (
                        "human-new",
                        "FD-0118",
                        "session-open",
                        "leandro",
                        "msg-new",
                        1000.125,
                        1000.25,
                        "new",
                        "received",
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        "none",
                    ),
                    (
                        "human-active",
                        "FD-0119",
                        "session-open",
                        "leandro",
                        "msg-active",
                        1001.125,
                        1001.25,
                        "active",
                        "in_progress",
                        "worker",
                        454,
                        1100.5,
                        None,
                        None,
                        None,
                        "none",
                    ),
                    (
                        "human-uncertain",
                        "FD-0120",
                        "session-open",
                        "leandro",
                        "msg-uncertain",
                        1002.125,
                        1002.25,
                        "uncertain",
                        "uncertain",
                        "lost-worker",
                        455,
                        1020.5,
                        None,
                        "uncertain outcome",
                        1021.0,
                        "none",
                    ),
                    (
                        "human-answer",
                        "FD-0121",
                        "session-open",
                        "leandro",
                        "msg-answer",
                        1003.125,
                        1003.25,
                        "answer",
                        "answered",
                        "worker",
                        456,
                        1110.5,
                        "canonical answer Ω",
                        None,
                        1050.75,
                        "output_uncertain",
                    ),
                    (
                        "human-failed",
                        "FD-0122",
                        "session-closed",
                        "leandro",
                        "msg-failed",
                        200.125,
                        200.25,
                        "failure",
                        "failed",
                        "worker",
                        450,
                        250.5,
                        None,
                        "synthetic failure",
                        251.0,
                        "none",
                    ),
                    (
                        "human-expired",
                        "FD-0123",
                        "session-closed",
                        "leandro",
                        "msg-expired",
                        300.125,
                        300.25,
                        "expired",
                        "expired",
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        "cancelled",
                    ),
                ),
            )
            db.executemany(
                "INSERT INTO human_outbox VALUES (?,?,?,?,?,?,?)",
                (
                    (
                        8,
                        "human-answer",
                        1,
                        "answer fragment 1",
                        "socket_sent",
                        1051.125,
                        1051.25,
                    ),
                    (
                        19,
                        "human-answer",
                        2,
                        "answer fragment 2",
                        "output_uncertain",
                        1052.5,
                        None,
                    ),
                    (42, "human-answer", 3, "answer fragment 3", "pending", None, None),
                    (
                        51,
                        "human-expired",
                        1,
                        "cancelled fragment",
                        "cancelled",
                        500.5,
                        None,
                    ),
                ),
            )
    path.chmod(0o600)
    return path


def test_readonly_snapshot_preserves_complete_peer_and_human_rows(config, tmp_path):
    path = snapshot(tmp_path, config)
    original = path.read_bytes()
    info = path.stat()
    result = migration._read_snapshot(config, path, checksum(path))
    assert path.read_bytes() == original
    assert path.stat().st_mtime_ns == info.st_mtime_ns
    assert len(result.rows["human_requests"]) == 6
    assert result.rows["human_meta"] == (("reference", 123), ("fence", 456))
    assert result.rows["requests"][1][7] == "Resposta 🙂\nwith lines"
    assert result.transport_attested is False
    assert {p.name for p in path.parent.iterdir()} == {path.name}


@pytest.mark.parametrize("expected", [None, "", "abc", "g" * 64, "0" * 64])
def test_checksum_is_mandatory_and_must_match(config, tmp_path, expected):
    path = snapshot(tmp_path, config)
    with pytest.raises(migration.MigrationError, match="SHA-256"):
        migration._read_snapshot(config, path, expected)


@pytest.mark.parametrize(
    "mutation",
    [
        "file_mode",
        "dir_mode",
        "symlink",
        "parent_symlink",
        "hardlink",
        "wal",
        "shm",
        "journal",
        "installed",
    ],
)
def test_refuses_nonprivate_or_live_sources(config, tmp_path, mutation):
    path = snapshot(tmp_path, config)
    expected = checksum(path)
    if mutation == "file_mode":
        path.chmod(0o644)
    elif mutation == "dir_mode":
        path.parent.chmod(0o755)
    elif mutation == "symlink":
        link = path.with_name("link.sqlite3")
        link.symlink_to(path)
        path = link
    elif mutation == "parent_symlink":
        link = tmp_path / "linked-dir"
        link.symlink_to(path.parent, target_is_directory=True)
        path = link / path.name
    elif mutation == "hardlink":
        os.link(path, path.with_name("hardlink.sqlite3"))
    elif mutation in {"wal", "shm", "journal"}:
        path.with_name(path.name + "-" + mutation).touch(mode=0o600)
    elif mutation == "installed":
        config = replace(config, state_dir=path.parent)
        path = path.rename(path.parent / "mailbox.sqlite3")
    with pytest.raises(migration.MigrationError):
        migration._read_snapshot(config, path, expected)


def test_wrong_owner_is_rejected_without_opening_database(
    config, tmp_path, monkeypatch
):
    path = snapshot(tmp_path, config)
    monkeypatch.setattr(migration.os, "getuid", lambda: path.stat().st_uid + 1)
    with pytest.raises(migration.MigrationError, match="owned"):
        migration._read_snapshot(config, path, checksum(path))


def test_nonregular_snapshot_is_rejected_without_blocking(config, tmp_path):
    folder = tmp_path / "offline"
    folder.mkdir(mode=0o700)
    path = folder / "snapshot.sqlite3"
    os.mkfifo(path, mode=0o600)
    with pytest.raises(migration.MigrationError, match="regular file"):
        migration._read_snapshot(config, path, "0" * 64)


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE unexpected (id TEXT)",
        "ALTER TABLE requests ADD COLUMN extra TEXT",
        "CREATE VIEW extra AS SELECT * FROM requests",
        "CREATE TRIGGER extra AFTER INSERT ON requests BEGIN DELETE FROM outbox; END",
        "DROP TABLE human_outbox",
        "DROP INDEX human_request_session",
        "PRAGMA user_version=99",
        "UPDATE meta SET value='wrong' WHERE key='identity'",
        "UPDATE meta SET value='wrong' WHERE key='human_transport_identity'",
        "INSERT INTO meta VALUES ('unexpected','metadata')",
        "UPDATE human_sessions SET channel='#wrong'",
        "UPDATE human_requests SET fence='not an integer'",
    ],
)
def test_unknown_schema_or_identity_or_type_fails_closed(config, tmp_path, statement):
    path = snapshot(tmp_path, config)
    with sqlite3.connect(path) as db:
        db.execute(statement)
    with pytest.raises(migration.MigrationError):
        migration._read_snapshot(config, path, checksum(path), attest=True)


def test_legacy_transport_requires_attestation_and_never_overrides_binding(
    config, tmp_path
):
    path = snapshot(tmp_path, config, humans=False, transport=False)
    with pytest.raises(migration.MigrationError, match="attestation"):
        migration._read_snapshot(config, path, checksum(path))
    assert migration._read_snapshot(
        config, path, checksum(path), attest=True
    ).transport_attested
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO meta VALUES ('human_transport_identity', 'wrong')")
    with pytest.raises(migration.MigrationError, match="different IRC transport"):
        migration._read_snapshot(config, path, checksum(path), attest=True)


def test_human_snapshot_cannot_use_legacy_transport_attestation(config, tmp_path):
    path = snapshot(tmp_path, config, transport=False)
    with pytest.raises(migration.MigrationError, match="missing its transport"):
        migration._read_snapshot(config, path, checksum(path), attest=True)


def test_detects_source_change_during_read(config, tmp_path, monkeypatch):
    path = snapshot(tmp_path, config)
    expected = checksum(path)
    original = migration._validate_database

    def changed(*args):
        result = original(*args)
        with sqlite3.connect(path) as db:
            db.execute("UPDATE rate_limits SET count=5")
        return result

    monkeypatch.setattr(migration, "_validate_database", changed)
    with pytest.raises(migration.MigrationError, match="changed"):
        migration._read_snapshot(config, path, expected)


def test_validation_errors_do_not_expose_source_path_or_rows(config, tmp_path):
    path = snapshot(tmp_path, config)
    path.write_bytes(b"synthetic-private-payload-not-sqlite")
    with pytest.raises(migration.MigrationError) as caught:
        migration._read_snapshot(config, path, checksum(path))
    assert str(tmp_path) not in str(caught.value)
    assert "synthetic-private-payload" not in str(caught.value)


def test_requires_postgres_and_quiescence_before_target_connection(
    config, tmp_path, monkeypatch
):
    def forbidden(*args, **kwargs):
        pytest.fail("destination must not be opened")

    monkeypatch.setattr(database, "postgres_connection", forbidden)
    with pytest.raises(migration.MigrationError, match="storage_backend"):
        migration.migrate_sqlite(
            config, Path("absent"), "0" * 64, confirm_quiesced=True
        )
    with pytest.raises(migration.MigrationError, match="writers are stopped"):
        migration.migrate_sqlite(
            replace(config, storage_backend="postgresql"), Path("absent"), "0" * 64
        )


def test_postgres_import_preserves_all_fields_ids_and_hold(
    postgres_config, tmp_path, capsys
):
    config = postgres_config
    path = snapshot(tmp_path, config)
    original = path.read_bytes()
    frozen = migration._read_snapshot(config, path, checksum(path))
    database.initialize_postgres(config)
    result = migration.migrate_sqlite(
        config, path, checksum(path), confirm_quiesced=True
    )
    assert result["counts"] == {
        "meta": 2,
        "requests": 4,
        "outbox": 2,
        "fragments": 2,
        "rate_limits": 2,
        "human_meta": 2,
        "human_sessions": 2,
        "human_requests": 6,
        "human_outbox": 4,
    }
    assert result["migration_hold"] == "reconciliation_required"
    assert result["legacy_transport_attested"] is False
    assert result["source_sha256"] == checksum(path)
    with database.postgres_connection(config, allow_hold=True) as db:
        for table, rows in frozen.rows.items():
            copied = tuple(
                tuple(row)
                for row in db.execute(
                    f'SELECT {migration._columns_sql(database.TABLE_COLUMNS[table])} FROM "{table}"'
                ).fetchall()
            )
            if table == "meta":
                copied = tuple(row for row in copied if row[0] in dict(rows))
            assert set(copied) == set(rows), table
        meta = dict(db.execute("SELECT key,value FROM meta").fetchall())
        assert meta["migration_source_sha256"] == result["source_sha256"]
        assert meta["migration_row_sha256"] == result["row_sha256"]
        assert (
            db.execute(
                "INSERT INTO outbox(request_id,line) VALUES ('next','synthetic') RETURNING id"
            ).fetchone()[0]
            == 18
        )
        assert (
            db.execute(
                "INSERT INTO human_outbox(request_id,part,line) VALUES ('next',1,'synthetic') RETURNING id"
            ).fetchone()[0]
            == 52
        )
    with pytest.raises(ValueError, match="held"):
        Mailbox(config)
    assert path.read_bytes() == original
    assert capsys.readouterr().out == ""
    assert "canonical answer" not in str(result)


def test_postgres_legacy_import_records_attestation_and_keeps_transport(
    postgres_config, tmp_path
):
    config = postgres_config
    path = snapshot(tmp_path, config, humans=False, transport=False)
    database.initialize_postgres(config)
    result = migration.migrate_sqlite(
        config,
        path,
        checksum(path),
        confirm_quiesced=True,
        attest_legacy_transport=True,
    )
    assert result["legacy_transport_attested"] is True
    with database.postgres_connection(config, allow_hold=True) as db:
        meta = dict(db.execute("SELECT key,value FROM meta").fetchall())
        assert meta["human_transport_identity"] == database.transport_identity(config)
        assert meta["migration_transport_attested"] == "true"
        assert db.execute("SELECT count(*) FROM human_requests").fetchone()[0] == 0


@pytest.mark.parametrize("connected", [True, False])
def test_postgres_import_preserves_actual_legacy_heartbeat(
    postgres_config, tmp_path, connected
):
    config = postgres_config
    source_config = replace(config, storage_backend="sqlite", database_url_env=None)
    box = Mailbox(source_config)
    box.heartbeat(connected)
    folder = tmp_path / "offline"
    folder.mkdir(mode=0o700)
    path = folder / "snapshot.sqlite3"
    with sqlite3.connect(box.path) as installed, sqlite3.connect(path) as frozen:
        installed.backup(frozen)
        expected = frozen.execute(
            "SELECT value FROM meta WHERE key='heartbeat'"
        ).fetchone()[0]
    path.chmod(0o600)
    database.initialize_postgres(config)
    result = migration.migrate_sqlite(
        config,
        path,
        checksum(path),
        confirm_quiesced=True,
        attest_legacy_transport=True,
    )
    assert result["counts"]["meta"] == 2
    with database.postgres_connection(config, allow_hold=True) as db:
        assert (
            db.execute("SELECT value FROM meta WHERE key='heartbeat'").fetchone()[0]
            == expected
        )
        assert (
            db.execute("SELECT value FROM meta WHERE key='migration_hold'").fetchone()[
                0
            ]
            == "reconciliation_required"
        )


def test_postgres_import_refuses_running_transport(postgres_config, tmp_path):
    config = postgres_config
    path = snapshot(tmp_path, config)
    database.initialize_postgres(config)
    box = Mailbox(config)
    with box.database.transport_owner():
        with pytest.raises(migration.MigrationError, match="transport is active"):
            migration.migrate_sqlite(
                config, path, checksum(path), confirm_quiesced=True
            )
    with database.postgres_connection(config, allow_hold=True) as db:
        assert db.execute("SELECT count(*) FROM requests").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM meta").fetchone()[0] == 3


@pytest.mark.parametrize("occupied", ["requests", "human_meta", "held", "unknown_meta"])
def test_postgres_nonempty_targets_are_unchanged(postgres_config, tmp_path, occupied):
    config = postgres_config
    path = snapshot(tmp_path, config)
    database.initialize_postgres(config)
    with database.postgres_connection(config, allow_hold=True) as db:
        if occupied == "requests":
            db.execute(
                "INSERT INTO requests VALUES ('existing','out','peer','peer',123,'keep','pending',NULL,0)"
            )
        elif occupied == "human_meta":
            db.execute("INSERT INTO human_meta VALUES ('fence',99)")
        else:
            key = "migration_hold" if occupied == "held" else "unexpected"
            db.execute("INSERT INTO meta VALUES (?, 'keep')", (key,))
        before = {
            table: tuple(
                tuple(row) for row in db.execute(f'SELECT * FROM "{table}"').fetchall()
            )
            for table in database.TABLE_COLUMNS
        }
    with pytest.raises(migration.MigrationError, match="empty|initialized"):
        migration.migrate_sqlite(config, path, checksum(path), confirm_quiesced=True)
    with database.postgres_connection(config, allow_hold=True) as db:
        assert before == {
            table: tuple(
                tuple(row) for row in db.execute(f'SELECT * FROM "{table}"').fetchall()
            )
            for table in database.TABLE_COLUMNS
        }


def test_postgres_failure_rolls_back_every_table_and_sanitizes(
    postgres_config, tmp_path, monkeypatch
):
    config = postgres_config
    path = snapshot(tmp_path, config)
    database.initialize_postgres(config)

    def fail(_db):
        raise RuntimeError("dsn=password payload=canonical answer")

    monkeypatch.setattr(database, "reset_postgres_sequences", fail)
    with pytest.raises(migration.MigrationError) as caught:
        migration.migrate_sqlite(config, path, checksum(path), confirm_quiesced=True)
    assert "password" not in str(caught.value)
    assert "canonical answer" not in str(caught.value)
    assert caught.value.__suppress_context__
    with database.postgres_connection(config, allow_hold=True) as db:
        for table in database.TABLE_COLUMNS:
            assert db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] == (
                3 if table == "meta" else 0
            )


def test_postgres_digest_mismatch_rolls_back_import(
    postgres_config, tmp_path, monkeypatch
):
    config = postgres_config
    path = snapshot(tmp_path, config)
    database.initialize_postgres(config)
    original = migration._digest
    calls = 0

    def corrupt(rows):
        nonlocal calls
        calls += 1
        return "0" * 64 if calls == 10 else original(rows)

    monkeypatch.setattr(migration, "_digest", corrupt)
    with pytest.raises(migration.MigrationError, match="digest"):
        migration.migrate_sqlite(config, path, checksum(path), confirm_quiesced=True)
    with database.postgres_connection(config, allow_hold=True) as db:
        assert db.execute("SELECT count(*) FROM requests").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM human_requests").fetchone()[0] == 0


def test_postgres_competing_imports_serialize_and_never_duplicate(
    postgres_config, tmp_path
):
    config = postgres_config
    path = snapshot(tmp_path, config)
    expected = checksum(path)
    database.initialize_postgres(config)

    def run():
        try:
            return migration.migrate_sqlite(
                config, path, expected, confirm_quiesced=True
            )
        except migration.MigrationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert sum(result is not None for result in results) == 1
    with database.postgres_connection(config, allow_hold=True) as db:
        assert db.execute("SELECT count(*) FROM human_requests").fetchone()[0] == 6
        assert db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 2


def test_postgres_uncertain_commit_is_not_retried(
    postgres_config, tmp_path, monkeypatch
):
    config = postgres_config
    path = snapshot(tmp_path, config)
    database.initialize_postgres(config)
    original = database.postgres_connection
    calls = 0

    @contextmanager
    def lost_commit(*args, **kwargs):
        nonlocal calls
        calls += 1
        with original(*args, **kwargs) as db:
            yield db
        # Model a committed transaction whose acknowledgement was lost.
        raise RuntimeError("secret connection detail")

    monkeypatch.setattr(database, "postgres_connection", lost_commit)
    with pytest.raises(
        migration.MigrationError, match="commit outcome is uncertain.*do not retry"
    ) as caught:
        migration.migrate_sqlite(config, path, checksum(path), confirm_quiesced=True)
    assert "secret" not in str(caught.value)
    assert calls == 1
    with original(config, allow_hold=True) as db:
        assert (
            db.execute("SELECT value FROM meta WHERE key='migration_hold'").fetchone()[
                0
            ]
            == "reconciliation_required"
        )
        assert db.execute(
            "SELECT value FROM meta WHERE key='migration_source_sha256'"
        ).fetchone()[0] == checksum(path)
