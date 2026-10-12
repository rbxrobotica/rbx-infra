"""PostgreSQL tests run only against the explicitly provisioned ephemeral fixture."""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import pytest

from strategos_irc_bot.agent_protocol import Envelope, parse_envelope
from strategos_irc_bot.agent_store import Mailbox
from strategos_irc_bot.human_store import HumanMailbox
from strategos_irc_bot.mailbox_database import (
    POSTGRES_FAILURE,
    initialize_postgres,
    postgres_connection,
    postgres_parameters,
    reset_postgres_sequences,
)


def initialized(config):
    initialize_postgres(config)
    return Mailbox(config)


def open_session(box, now=1000):
    return box.open_session(
        "flightdeck-dev",
        sorted(box.config.operators)[0],
        "rbx-flightdeck",
        "a" * 40,
        "documentary-model",
        now=now,
    )


def receive(box, msgid="Opaque-CaseSensitive-1", now=1001):
    return box.receive(
        "flightdeck-dev",
        sorted(box.config.operators)[0],
        msgid,
        now,
        "Qual é o limite da consulta?",
        now=now,
    )


def test_postgres_requires_explicit_init_and_never_touches_local_state(postgres_config):
    with pytest.raises(ValueError, match="PostgreSQL mailbox operation failed"):
        Mailbox(postgres_config)
    assert not postgres_config.state_dir.exists()
    assert initialize_postgres(postgres_config) == {
        "schema_version": "1",
        "initialized": True,
    }
    assert initialize_postgres(postgres_config)["initialized"] is False
    box = Mailbox(postgres_config)
    assert box.path is None
    assert not postgres_config.state_dir.exists()
    with box.connection() as db:
        assert (
            db.execute("SHOW transaction_isolation").fetchone()[0] == "read committed"
        )
        assert (
            dict(db.execute("SELECT key,value FROM meta").fetchall())["schema_version"]
            == "1"
        )


def test_postgres_identity_transport_version_and_hold_guards(postgres_config):
    initialized(postgres_config)
    for changed in (
        {"agent_id": "another"},
        {"account": "another"},
        {"channel": "#other"},
        {"server": "different.example:6697"},
        {"tls": not postgres_config.tls},
    ):
        with pytest.raises(ValueError, match="different"):
            Mailbox(replace(postgres_config, **changed))
    with postgres_connection(postgres_config) as db:
        db.execute(
            "INSERT INTO meta VALUES ('migration_hold','reconciliation_required')"
        )
    with pytest.raises(ValueError, match="held for offline reconciliation"):
        Mailbox(postgres_config)
    with postgres_connection(postgres_config, allow_hold=True) as db:
        db.execute("DELETE FROM meta WHERE key='migration_hold'")
        db.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
    with pytest.raises(ValueError, match="required version"):
        Mailbox(postgres_config)


def test_postgres_peer_roundtrip_preserves_ids_fragments_retry_and_ack(
    postgres_config_factory,
):
    config = postgres_config_factory(
        agent_id="local",
        account="rbx-local",
        peers={"remote": "rbx-remote"},
    )
    remote_config = postgres_config_factory(
        agent_id="remote",
        account="rbx-remote",
        peers={"local": "rbx-local"},
    )
    local, remote = initialized(config), initialized(remote_config)
    request_id = local.send("remote", "Verificar o backup")
    question = parse_envelope(local.next_outgoing()[1])
    assert remote.receive(question, "rbx-local")
    assert remote.inbox()[0]["id"] == request_id
    response = "Resposta com Unicode, autorização e recuperação. " * 10
    remote.reply(request_id, response)
    for _ in range(2):
        frames = []
        while outgoing := remote.next_outgoing():
            frames.append(parse_envelope(outgoing[1]))
            remote.sent(outgoing[0])
        if _ == 0:
            assert not remote.receive(question, "rbx-local")
        else:
            for frame in reversed(frames):
                local.receive(frame, "rbx-remote")
    assert local.inbox()[0]["response"] == response.strip()
    Mailbox(config).acknowledge(request_id)
    assert local.inbox() == []


def test_postgres_human_restart_deduplication_claim_and_at_most_once_output(
    postgres_config,
):
    box = HumanMailbox(initialized(postgres_config))
    open_session(box)
    question = receive(box)
    restarted = HumanMailbox(Mailbox(postgres_config))
    duplicate = receive(restarted)
    assert duplicate["duplicate"] and duplicate["id"] == question["id"]
    assert receive(restarted, msgid="opaque-casesensitive-1")["id"] != question["id"]
    claim = restarted.claim(question["id"], "consumer-1", now=1002)
    with pytest.raises(ValueError, match="fence"):
        restarted.complete(
            question["id"], "consumer-1", claim["fence"] + 1, "Resposta", now=1003
        )
    text = "Limite documentado de três perguntas, sem execução automática. " * 10
    result = restarted.complete(
        question["id"], "consumer-1", claim["fence"], text, now=1003
    )
    assert result["response"] == text.strip()
    assert result["delivery_status"] == "pending"
    lines = []
    assert restarted.dispatch_pending(lines.extend, now=1004)
    assert len(lines) == 1
    assert (
        HumanMailbox(Mailbox(postgres_config)).dispatch_pending(lines.extend, now=1005)
        is None
    )
    assert len(lines) == 1
    assert (
        restarted.reference(question["reference"], now=1005)["delivery_status"]
        == "output_uncertain"
    )


def test_postgres_concurrent_claim_has_one_winner_and_no_reclaim(postgres_config):
    box = HumanMailbox(initialized(postgres_config))
    open_session(box)
    question = receive(box)

    def claim(owner):
        try:
            return HumanMailbox(Mailbox(postgres_config)).claim(
                question["id"], owner, now=1002
            )
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("consumer-a", "consumer-b")))
    assert sum(result is not None for result in results) == 1
    assert box.reference(question["reference"], now=1100)["status"] == "uncertain"
    with pytest.raises(ValueError, match="unavailable"):
        box.claim(question["id"], "consumer-a", now=1101)


def test_postgres_complete_rolls_back_response_and_frames_together(postgres_config):
    box = HumanMailbox(initialized(postgres_config))
    open_session(box)
    question = receive(box)
    claim = box.claim(question["id"], "consumer", now=1002)
    with postgres_connection(postgres_config) as db:
        # A deterministic database-side error after one frame has been inserted.
        db.execute(
            """CREATE FUNCTION irc_mailbox.test_reject_second_frame() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN IF NEW.part=2 THEN RAISE EXCEPTION 'synthetic';
            END IF; RETURN NEW; END $$"""
        )
        db.execute("""CREATE TRIGGER reject_second_frame BEFORE INSERT ON human_outbox
            FOR EACH ROW EXECUTE FUNCTION irc_mailbox.test_reject_second_frame()""")
    with pytest.raises(ValueError, match="outcome may be uncertain"):
        box.complete(
            question["id"], "consumer", claim["fence"], "Resposta. " * 100, now=1003
        )
    with postgres_connection(postgres_config) as db:
        assert db.execute("SELECT count(*) FROM human_outbox").fetchone()[0] == 0
        row = db.execute(
            "SELECT status,response FROM human_requests WHERE id=?", (question["id"],)
        ).fetchone()
        assert row["status"] == "in_progress" and row["response"] is None


def test_postgres_deadline_is_refreshed_after_waiting_for_advisory_lock(
    postgres_config, monkeypatch
):
    box = HumanMailbox(initialized(postgres_config))
    open_session(box)
    question = receive(box)
    instant = [1002]
    monkeypatch.setattr("strategos_irc_bot.human_store.time.time", lambda: instant[0])
    entered = Event()

    def waiting_claim():
        entered.set()
        return box.claim(question["id"], "consumer")

    with ThreadPoolExecutor(max_workers=1) as pool:
        with postgres_connection(postgres_config):
            pending = pool.submit(waiting_claim)
            assert entered.wait(2)
            time.sleep(0.15)
            assert not pending.done()
            instant[0] = 1700
        with pytest.raises(ValueError, match="unavailable"):
            pending.result(timeout=3)


def test_postgres_daemon_owner_is_exclusive_and_uses_same_session(postgres_config):
    box = initialized(postgres_config)
    other = Mailbox(postgres_config)
    with box.database.transport_owner():
        with box.connection() as db:
            owner_pid = db.execute("SELECT pg_backend_pid()").fetchone()[0]
        with box.connection() as db:
            assert db.execute("SELECT pg_backend_pid()").fetchone()[0] == owner_pid
        with pytest.raises(ValueError, match="transport is already active"):
            with other.database.transport_owner():
                pytest.fail("second owner admitted")
        # Ownership uses another key; a CLI consumer can still transact.
        assert other.status()["agent_id"] == postgres_config.agent_id
    with other.database.transport_owner():
        assert other.status()["agent_id"] == postgres_config.agent_id


def test_postgres_lost_owner_connection_never_reconnects_or_creates_sqlite(
    postgres_config,
):
    box = initialized(postgres_config)
    with box.database.transport_owner():
        with box.connection() as db:
            pid = db.execute("SELECT pg_backend_pid()").fetchone()[0]
        with postgres_connection(postgres_config) as db:
            assert db.execute("SELECT pg_terminate_backend(?)", (pid,)).fetchone()[0]
        with pytest.raises(ValueError, match="outcome may be uncertain"):
            box.send(next(iter(postgres_config.peers)), "No reconnect")
        with pytest.raises(ValueError, match="outcome may be uncertain"):
            box.status()
        assert not postgres_config.state_dir.exists()


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://user:secret-fixture@remote.example/db?sslmode=disable",
        "postgresql://user:secret-fixture@remote.example/db?sslmode=require",
        "postgresql://user:secret-fixture@remote.example/db?sslmode=verify-ca",
        "postgresql://user:secret-fixture@remote.example/db?sslmode=verify-full&options=-csearch_path%3Dpublic",
        "postgresql://user:secret-fixture@remote.example/db?sslmode=verify-full&hostaddr=127.0.0.1",
        "service=hidden",
        "host=localhost user=user sslmode=disable",
        "host=localhost dbname=db sslmode=disable",
        "host=/tmp dbname=db user=user sslmode=disable",
        "host=localhost,remote.example dbname=db user=user sslmode=disable",
    ],
)
def test_postgres_rejects_unsafe_implicit_dsn_without_revealing_values(
    postgres_config, monkeypatch, dsn
):
    monkeypatch.setenv(postgres_config.database_url_env, dsn)
    with pytest.raises(ValueError) as failure:
        postgres_parameters(postgres_config)
    assert "secret-fixture" not in str(failure.value)
    assert "remote.example" not in str(failure.value)


def test_postgres_rejects_ambient_libpq_overrides(postgres_config, monkeypatch):
    with monkeypatch.context() as environment:
        environment.setenv("PGSERVICE", "hidden")
        with pytest.raises(ValueError, match="ambient"):
            postgres_parameters(postgres_config)


def test_postgres_connection_failure_is_redacted_and_never_falls_back(
    postgres_config, monkeypatch
):
    monkeypatch.setenv(
        postgres_config.database_url_env,
        "postgresql://secret-user:secret-password@127.0.0.1:1/secret-db?sslmode=disable",
    )
    with pytest.raises(ValueError) as failure:
        Mailbox(postgres_config)
    assert str(failure.value) == POSTGRES_FAILURE
    assert failure.value.__suppress_context__
    assert not postgres_config.state_dir.exists()


def test_postgres_runtime_role_operates_without_schema_ddl(
    postgres_config, monkeypatch
):
    from uuid import uuid4
    import psycopg
    from psycopg import sql

    initialized(postgres_config)
    role = "irc_runtime_" + uuid4().hex
    password = "synthetic-" + uuid4().hex
    with postgres_connection(postgres_config) as db:
        db.raw.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
        db.execute(f"GRANT USAGE ON SCHEMA irc_mailbox TO {role}")
        db.execute(
            f"GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA irc_mailbox TO {role}"
        )
        db.execute(f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA irc_mailbox TO {role}")
    try:
        original = os.environ[postgres_config.database_url_env]
        parameters = psycopg.conninfo.conninfo_to_dict(original)
        parameters["user"] = role
        parameters["password"] = password
        variable = "RBX_IRC_LIMITED_RUNTIME_TEST"
        monkeypatch.setenv(variable, psycopg.conninfo.make_conninfo(**parameters))
        runtime = replace(postgres_config, database_url_env=variable)
        box = HumanMailbox(Mailbox(runtime))
        open_session(box)
        question = receive(box)
        claim = box.claim(question["id"], "consumer", now=1002)
        assert (
            box.complete(
                question["id"], "consumer", claim["fence"], "Resposta", now=1003
            )["status"]
            == "answered"
        )
        with box.mailbox.connection() as db:
            assert not db.execute(
                "SELECT has_schema_privilege(current_user,'irc_mailbox','CREATE')"
            ).fetchone()[0]
        with pytest.raises(ValueError, match="operation failed"):
            with box.mailbox.connection() as db:
                db.execute("CREATE TABLE irc_mailbox.forbidden_runtime_ddl(id bigint)")
    finally:
        with postgres_connection(postgres_config) as db:
            db.execute(f"DROP OWNED BY {role}")
            db.execute(f"DROP ROLE {role}")


def test_postgres_import_sequence_reset_rolls_back_with_rows(postgres_config):
    initialized(postgres_config)
    with pytest.raises(RuntimeError, match="synthetic import failure"):
        with postgres_connection(postgres_config) as db:
            db.execute(
                "INSERT INTO outbox(id,request_id,line) VALUES (100,'fixture','line')"
            )
            reset_postgres_sequences(db)
            raise RuntimeError("synthetic import failure")
    with postgres_connection(postgres_config) as db:
        assert db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0
        assert (
            db.execute(
                "INSERT INTO outbox(request_id,line) VALUES ('after-rollback','line') RETURNING id"
            ).fetchone()[0]
            == 1
        )
        db.execute(
            "INSERT INTO outbox(id,request_id,line) VALUES (100,'fixture','line')"
        )
        reset_postgres_sequences(db)
    with postgres_connection(postgres_config) as db:
        assert (
            db.execute(
                "INSERT INTO outbox(request_id,line) VALUES ('after-commit','line') RETURNING id"
            ).fetchone()[0]
            == 101
        )
