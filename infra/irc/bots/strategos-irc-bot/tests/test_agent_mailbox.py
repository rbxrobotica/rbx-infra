import time
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from strategos_irc_bot.agent_config import AgentConfig, load_agent_config
from strategos_irc_bot.agent_protocol import Envelope, chunks, parse_envelope
from strategos_irc_bot.agent_store import Mailbox


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
        tmp_path / "local",
    )


def other(config, tmp_path):
    return replace(
        config,
        agent_id="thinkcentre",
        nick="rbx-thinkcentre",
        account="rbx-thinkcentre",
        peers={"local": "rbx-local"},
        state_dir=tmp_path / "thinkcentre",
    )


def test_two_agents_correlate_fragmented_unicode_reply_and_ack(config, tmp_path):
    sender, receiver = Mailbox(config), Mailbox(other(config, tmp_path))
    request_id = sender.send("thinkcentre", "Conferir o estado do backup")
    request = parse_envelope(sender.next_outgoing()[1])
    assert receiver.receive(request, "rbx-local")
    assert receiver.inbox()[0]["id"] == request_id
    assert receiver.inbox()[0]["untrusted_content"] is True
    assert not receiver.receive(request, "rbx-local")
    result = "Verificação concluída. " * 30
    receiver.reply(request_id, result)
    envelopes = []
    while queued := receiver.next_outgoing():
        envelopes.append(parse_envelope(queued[1]))
        receiver.sent(queued[0])
    for envelope in reversed(envelopes):
        assert sender.receive(envelope, "rbx-thinkcentre")
        assert not sender.receive(envelope, "rbx-thinkcentre")
    assert sender.inbox()[0]["response"] == result.strip()
    assert receiver.inbox() == []
    sender.acknowledge(request_id)
    assert sender.inbox() == []
    with pytest.raises(ValueError, match="already answered"):
        receiver.reply(request_id, "segunda resposta")


def test_account_binding_unknown_accounts_self_and_wrong_target(config, tmp_path):
    box = Mailbox(config)
    now = int(time.time())
    question = Envelope("ask", "local", "a" * 32, now, "status?")
    assert not box.receive(question, "unknown")
    assert not box.receive(question, "rbx-local")
    assert not box.receive(replace(question, target="thinkcentre"), "leandro")
    assert box.receive(question, "LEANDRO")
    assert not box.receive(replace(question, text="different query"), "rbx-thinkcentre")
    request_id = box.send("thinkcentre", "status?")
    request = parse_envelope(box.next_outgoing()[1])
    reply = Envelope("reply", "local", request_id, request.timestamp, "ok")
    assert not box.receive(reply, "leandro")
    assert not box.receive(replace(reply, timestamp=now + 1), "rbx-thinkcentre")
    assert box.receive(reply, "rbx-thinkcentre")


def test_expiry_future_replay_rate_and_capacity(config):
    box = Mailbox(replace(config, capacity=10))
    now = int(time.time())
    base = Envelope("ask", "local", "a" * 32, now, "status?")
    assert not box.receive(replace(base, timestamp=now - 601), "leandro", now)
    assert not box.receive(replace(base, timestamp=now + 31), "leandro", now)
    for index in range(6):
        assert box.receive(replace(base, request_id=f"{index:032x}"), "leandro", now)
    assert not box.receive(replace(base, request_id="b" * 32), "leandro", now)
    for _ in range(4):
        box.send("thinkcentre", "status?")
    with pytest.raises(ValueError, match="full"):
        box.send("thinkcentre", "status?")
    with box.connection() as db:
        db.execute(
            "UPDATE requests SET timestamp=?", (now - config.retention_seconds - 1,)
        )
    assert box.inbox() == []
    assert box.status()["requests"] == {}
    assert box.status()["queued_frames"] == 0


def test_retry_does_not_repeat_work_and_resends_cached_answer(
    config, tmp_path, monkeypatch
):
    now = int(time.time())
    monkeypatch.setattr("time.time", lambda: now)
    sender, receiver = Mailbox(config), Mailbox(other(config, tmp_path))
    request_id = sender.send("thinkcentre", "status?")
    queued = sender.next_outgoing()
    request = parse_envelope(queued[1])
    sender.sent(queued[0])
    assert sender.next_outgoing() is None
    now += 16
    assert sender.next_outgoing()[1] == queued[1]
    receiver.receive(request, "rbx-local")
    receiver.reply(request_id, "ok")
    reply = receiver.next_outgoing()
    receiver.sent(reply[0])
    assert receiver.next_outgoing() is None
    assert not receiver.receive(request, "rbx-local")
    assert receiver.next_outgoing()[1] == reply[1]
    sender.receive(parse_envelope(reply[1]), "rbx-thinkcentre")
    assert sender.next_outgoing() is None
    assert receiver.inbox() == []


def test_conflicting_fragment_totals_and_expired_reply(config):
    box = Mailbox(config)
    request_id = box.send("thinkcentre", "status?")
    request = parse_envelope(box.next_outgoing()[1])
    first = Envelope("reply", "local", request_id, request.timestamp, "one ", 1, 2)
    assert box.receive(first, "rbx-thinkcentre")
    assert not box.receive(replace(first, part=2, total=3), "rbx-thinkcentre")
    assert not box.receive(
        replace(first, part=2), "rbx-thinkcentre", request.timestamp + 601
    )
    assert box.inbox() == []


def test_private_storage_restart_and_identity_binding(config, tmp_path):
    box = Mailbox(config)
    request_id = box.send("thinkcentre", "status?")
    assert (config.state_dir.stat().st_mode & 0o777) == 0o700
    assert (box.path.stat().st_mode & 0o777) == 0o600
    assert Mailbox(config).next_outgoing() == box.next_outgoing()
    assert request_id in box.next_outgoing()[1]
    with pytest.raises(ValueError, match="different agent"):
        Mailbox(replace(config, agent_id="other"))
    box.path.chmod(0o644)
    with pytest.raises(ValueError, match="private file"):
        Mailbox(config)
    link = tmp_path / "link"
    link.symlink_to(config.state_dir)
    with pytest.raises(ValueError, match="private directory"):
        Mailbox(replace(config, state_dir=link))


@pytest.mark.parametrize(
    "text",
    [
        "!ask local bad 12 hi",
        "!ask local " + "a" * 32 + " nope hi",
        "!reply local " + "a" * 32 + " 12 3/2 hi",
        "!ask local " + "a" * 32 + " 12 a\nb",
        "!ask local " + "a" * 32 + " 12 " + "á" * 121,
        "normal chat",
        "!reply local " + "a" * 32 + " 12 1/9 hi",
    ],
)
def test_malformed_or_oversized_envelopes_are_rejected(text):
    with pytest.raises(ValueError):
        parse_envelope(text)


def test_protocol_utf8_boundary_and_control_injection(config):
    text = "🙂" * 120
    assert "".join(chunks(text)) == text
    assert all(len(part.encode()) <= 240 for part in chunks(text))
    with pytest.raises(ValueError):
        chunks("x" * 1921)
    with pytest.raises(ValueError):
        Mailbox(config).send("thinkcentre", "x\r\nPRIVMSG #public :injected")
    with pytest.raises(ValueError):
        Mailbox(config).send("unknown", "status?")


@pytest.mark.parametrize(
    "key,value",
    [
        ("tls", "false"),
        ("server", "0.0.0.0:6667"),
        ("channel", "#rbx-agents\r\nJOIN #public"),
        ("password_env", "HOME"),
        ("peers", {"local": "rbx-local"}),
        ("operators", ["rbx-thinkcentre"]),
        ("state_dir", "relative"),
        ("ttl_seconds", True),
        ("tls_verify", False),
    ],
)
def test_config_rejects_unsafe_values(key, value, tmp_path):
    example = Path(__file__).parents[1] / "agent.local.example.yaml"
    data = yaml.safe_load(example.read_text())
    data[key] = value
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    # HOME is syntactically valid, but deliberately forbidden as a password env.
    with pytest.raises(ValueError):
        load_agent_config(path)
