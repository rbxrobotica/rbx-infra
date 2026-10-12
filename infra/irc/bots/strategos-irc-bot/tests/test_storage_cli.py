import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from strategos_irc_bot.agent_cli import main
from strategos_irc_bot.agent_config import load_agent_config
from strategos_irc_bot.agent_store import Mailbox
from strategos_irc_bot.mailbox_database import initialize_postgres, postgres_connection
from strategos_irc_bot.storage_cli import release_migration, storage_status


def config_file(tmp_path, **values):
    example = Path(__file__).parents[1] / "agent.local.example.yaml"
    data = yaml.safe_load(example.read_text())
    data.update(values)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


@pytest.mark.parametrize(
    "values",
    [
        {"storage_backend": "postgres"},
        {"storage_backend": "postgresql"},
        {"storage_backend": "postgresql", "database_url_env": "PGDATABASE"},
        {"storage_backend": "postgresql", "database_url_env": "postgresql://secret"},
        {"database_url_env": "RBX_IRC_DATABASE_URL"},
        {"storage_backend": "postgresql", "database_url_env": ["RBX_DSN"]},
    ],
)
def test_config_rejects_ambiguous_storage(tmp_path, values):
    with pytest.raises(ValueError):
        load_agent_config(config_file(tmp_path, **values))


def test_config_defaults_remain_sqlite_and_human_disabled(tmp_path):
    config = load_agent_config(config_file(tmp_path))
    assert config.storage_backend == "sqlite"
    assert config.database_url_env is None
    assert not config.human_queries
    config = load_agent_config(
        config_file(
            tmp_path,
            storage_backend="postgresql",
            database_url_env="RBX_IRC_DATABASE_URL",
        )
    )
    assert config.storage_backend == "postgresql"
    assert not config.human_queries


def test_admin_cli_can_initialize_before_runtime_schema_exists(
    postgres_config, tmp_path, monkeypatch, capsys
):
    path = config_file(
        tmp_path,
        storage_backend="postgresql",
        database_url_env=postgres_config.database_url_env,
        state_dir=str(postgres_config.state_dir),
    )
    monkeypatch.setattr(
        "sys.argv", ["rbx-agent-irc", "--config", str(path), "storage", "initialize"]
    )
    main()
    assert json.loads(capsys.readouterr().out)["initialized"] is True
    assert not postgres_config.state_dir.exists()
    monkeypatch.setattr(
        "sys.argv", ["rbx-agent-irc", "--config", str(path), "storage", "status"]
    )
    main()
    assert json.loads(capsys.readouterr().out)["counts"]["requests"] == 0


def hold(config):
    with postgres_connection(config) as db:
        db.execute(
            "INSERT INTO meta VALUES ('migration_hold','reconciliation_required')"
        )


def test_release_requires_confirmation_and_quiescent_reconciled_state(postgres_config):
    config = replace(postgres_config, human_queries=False)
    initialize_postgres(config)
    box = Mailbox(config)
    box.heartbeat(True)
    hold(config)
    before = storage_status(config)
    assert not any(before["release_blockers"].values())
    with pytest.raises(ValueError, match="confirmation"):
        release_migration(config)
    assert storage_status(config) == before
    result = release_migration(config, confirm_reconciled=True)
    assert result == {
        "migration_hold": None,
        "released": True,
        "transport_started": False,
    }
    assert not Mailbox(config).status()["connected"]
    with pytest.raises(ValueError, match="no recognized"):
        release_migration(config, confirm_reconciled=True)


def test_read_only_admin_inspection_does_not_expire_or_dispatch(postgres_config):
    initialize_postgres(postgres_config)
    box = Mailbox(postgres_config)
    request = box.send("thinkcentre", "synthetic retained request")
    queued = box.next_outgoing()
    box.sent(queued[0])
    with box.connection() as db:
        db.execute("UPDATE requests SET timestamp=1 WHERE id=?", (request,))
    hold(postgres_config)
    status = storage_status(postgres_config)
    assert status["release_blockers"]["peer_pending"] == 1
    assert status["release_blockers"]["peer_unsent"] == 0
    assert "synthetic" not in json.dumps(status)
    with pytest.raises(ValueError, match="unresolved"):
        release_migration(postgres_config, confirm_reconciled=True)
    assert storage_status(postgres_config) == status


@pytest.mark.parametrize("kind", ["open_session", "uncertain", "pending_frame"])
def test_release_blocks_human_uncertainty(postgres_config, kind):
    from strategos_irc_bot.human_store import HumanMailbox
    import time

    initialize_postgres(postgres_config)
    box = Mailbox(postgres_config)
    human = HumanMailbox(box)
    session = human.open_session(
        "flightdeck-dev", "leandro", "rbx-flightdeck", "a" * 40, "fixture"
    )
    if kind != "open_session":
        received = human.receive(
            "flightdeck-dev", "leandro", "synthetic-msgid", time.time(), "question"
        )
        claimed = human.claim(received["id"], "fixture")
        if kind == "uncertain":
            human.fail(
                received["id"],
                "fixture",
                claimed["fence"],
                "synthetic uncertainty",
                uncertain=True,
            )
        else:
            human.complete(
                received["id"], "fixture", claimed["fence"], "synthetic answer"
            )
    hold(postgres_config)
    with pytest.raises(ValueError, match="unresolved"):
        release_migration(postgres_config, confirm_reconciled=True)
    assert storage_status(postgres_config)["migration_hold"]


def test_release_refuses_an_existing_daemon_owner(postgres_config):
    initialize_postgres(postgres_config)
    box = Mailbox(postgres_config)
    with box.database.transport_owner():
        hold(postgres_config)
        with pytest.raises(ValueError, match="stop the mailbox transport"):
            release_migration(postgres_config, confirm_reconciled=True)
