import asyncio
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from strategos_irc_bot.agent_config import AgentConfig, load_agent_config
from strategos_irc_bot.agent_store import Mailbox
from strategos_irc_bot.agent_transport import AgentTransport, ProtocolError
from strategos_irc_bot.bot import parse_irc_line
from strategos_irc_bot.human_protocol import HUMAN_CAPABILITIES


class Writer:
    def __init__(self):
        self.lines = []

    def write(self, value):
        self.lines.append(value.decode().rstrip("\r\n"))

    async def drain(self):
        pass


def config_for(tmp_path):
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
        tmp_path / "state",
        human_queries=True,
    )


def test_opt_in_config_is_strict_and_default_off(tmp_path):
    raw = yaml.safe_load(
        (Path(__file__).parents[1] / "agent.local.example.yaml").read_text()
    )
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.safe_dump(raw))
    assert not load_agent_config(path).human_queries
    for value in ["true", 1, None, [], {}]:
        path.write_text(yaml.safe_dump(raw | {"human_queries": value}))
        with pytest.raises(ValueError, match="boolean"):
            load_agent_config(path)
    path.write_text(yaml.safe_dump(raw | {"human_queries": True}))
    assert load_agent_config(path).human_queries


def test_human_mode_needs_all_negotiated_capabilities(tmp_path):
    async def scenario():
        cfg = config_for(tmp_path)
        bot = AgentTransport(cfg, Mailbox(cfg), "fixture")
        bot.writer = Writer()
        with pytest.raises(ProtocolError):
            await bot.handle(parse_irc_line(":server CAP * LS :account-tag sasl"))
        await bot.handle(
            parse_irc_line(
                ":server CAP * LS :account-tag sasl message-tags server-time"
            )
        )
        assert (
            bot.writer.lines[-1] == "CAP REQ :account-tag sasl message-tags server-time"
        )
        await bot.handle(parse_irc_line(":server CAP * ACK :account-tag sasl"))
        assert not bot.sasl_started
        await bot.handle(parse_irc_line(":server CAP * ACK :message-tags server-time"))
        assert bot.sasl_started
        with pytest.raises(ProtocolError):
            await bot.handle(parse_irc_line(":server CAP * DEL :server-time"))

    asyncio.run(scenario())


def test_untrusted_and_history_inputs_never_open_sessions_or_consume_slots(tmp_path):
    async def scenario():
        cfg = config_for(tmp_path)
        bot = AgentTransport(cfg, Mailbox(cfg), "fixture")
        bot.writer = Writer()
        now = time.time()
        stamp = (
            datetime.fromtimestamp(now, timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        tags = f"account=leandro;msgid=server-1;time={stamp}"

        def frame(
            *,
            metadata=tags,
            channel="#rbx-agents",
            nick="human",
            text="@flightdeck-dev Qual é o escopo?",
        ):
            return parse_irc_line(f"@{metadata} :{nick}!u@h PRIVMSG {channel} :{text}")

        bot.ready = bot.authenticated = True
        bot.acked.update(HUMAN_CAPABILITIES)
        await bot.handle(frame())
        assert bot.humans.inbox() == []
        session = bot.humans.open_session(
            "flightdeck-dev",
            "leandro",
            "rbx-flightdeck",
            "a" * 40,
            "gpt-6.1-sol",
            now=now - 1,
        )
        for message in [
            frame(metadata=tags.replace("leandro", "unknown"), nick="leandro"),
            frame(metadata=tags.replace("leandro", "rbx-thinkcentre")),
            frame(metadata=tags.replace("leandro", "rbx-local")),
            frame(metadata=tags + ";batch=history"),
            frame(metadata="account=leandro;+msgid=fake;+time=" + stamp),
            frame(metadata="account=leandro;msgid=server-1"),
            frame(metadata="account=leandro;time=" + stamp),
            frame(metadata=tags.replace(stamp, "2020-01-01T00:00:00.000Z")),
            frame(channel="rbx-local"),
            frame(channel="#other"),
            frame(nick="rbx-local"),
            frame(text="ambient @flightdeck-dev pergunta"),
        ]:
            await bot.handle(message)
        assert bot.humans.inbox() == []
        bot.ready = False
        await bot.handle(frame())
        bot.ready = True
        bot.authenticated = False
        await bot.handle(frame())
        bot.authenticated = True
        bot.acked.discard("message-tags")
        await bot.handle(frame())
        assert bot.humans.inbox() == []
        bot.acked.add("message-tags")
        await bot.handle(frame())
        await bot.handle(frame())
        assert len(bot.humans.inbox()) == 1
        assert bot.writer.lines == []
        bot.humans.close_session(session["id"])
        await bot.handle(frame(metadata=tags.replace("server-1", "server-2")))
        assert bot.humans.inbox() == []

    asyncio.run(scenario())


def test_default_disabled_creates_no_human_tables(tmp_path):
    cfg = replace(config_for(tmp_path), human_queries=False)
    box = Mailbox(cfg)
    bot = AgentTransport(cfg, box, "fixture")
    assert bot.humans is None
    with box.connection() as db:
        assert (
            db.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE 'human_%'"
            ).fetchall()
            == []
        )
