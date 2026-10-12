"""Loopback protocol acceptance with a simulated documentary worker only.

These tests prove wire negotiation, admission, persisted claims and readable
publication against a fake IRC server. They do not exercise Ergo, Halloy, TLS,
subscription inference, or the installed daemon.
"""

import asyncio
import base64
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
import re
import stat
import time

import pytest

from strategos_irc_bot.agent_config import AgentConfig
from strategos_irc_bot.agent_store import Mailbox
from strategos_irc_bot.agent_transport import AgentTransport

QUESTION = "Qual é a diferença entre aprovar uma Action e executar uma Mission?"
ANSWER = (
    "Aprovar uma Action registra uma decisão e seus limites. "
    "Executar uma Mission realiza o trabalho autorizado. "
    "Esta resposta sintética preserva acentuação, palavras e o símbolo 🧪. "
    "Ela descreve documentação de teste e não comprova ações em produção. "
) * 2


def server_time(seconds=None):
    return (
        datetime.fromtimestamp(
            time.time() if seconds is None else seconds, timezone.utc
        )
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def human_frame(
    msgid,
    *,
    question=QUESTION,
    account="leandro",
    channel="#rbx-agents",
    timestamp=None,
    extra_tags="",
    nick="human",
):
    tags = f"account={account};msgid={msgid};time={timestamp or server_time()}"
    return (
        f"@{tags}{extra_tags} :{nick}!user@fixture PRIVMSG {channel} "
        f":@flightdeck-dev {question}"
    )


class LoopbackIRC:
    """Small SASL fixture; PING barriers prove earlier frames were consumed."""

    def __init__(self):
        self.tasks = set()
        self.errors = []
        self.writer = None
        self.connections = 0
        self.lines = []
        self.pongs = set()
        self.barriers = 0
        self.bridge = None
        self.running = None

    async def serve(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        self.writer = writer
        self.connections += 1
        nick = account = None

        async def send(line):
            writer.write((line + "\r\n").encode())
            await writer.drain()

        try:
            while raw := await reader.readline():
                line = raw.decode("utf-8").rstrip("\r\n")
                if line == "CAP LS 302":
                    await send(":fixture CAP * LS * :account-tag sasl=PLAIN")
                    await send(":fixture CAP * LS :message-tags server-time")
                elif line.startswith("NICK "):
                    nick = line.split()[1]
                elif line.startswith("CAP REQ :"):
                    assert set(line.partition(" :")[2].split()) == {
                        "account-tag",
                        "sasl",
                        "message-tags",
                        "server-time",
                    }
                    await send(f":fixture CAP {nick} ACK :account-tag sasl")
                    await send(f":fixture CAP {nick} ACK :message-tags server-time")
                elif line == "AUTHENTICATE PLAIN":
                    await send("AUTHENTICATE +")
                elif line.startswith("AUTHENTICATE "):
                    identity, account, password = (
                        base64.b64decode(line.split()[1], validate=True)
                        .decode()
                        .split("\0")
                    )
                    assert identity == "" and account == nick == "rbx-local"
                    assert password == "synthetic-fixture-password"
                    await send(f":fixture 903 {nick} :SASL success")
                elif line == "CAP END":
                    assert account is not None
                    await send(f":fixture 001 {nick} :welcome")
                elif line == "JOIN #rbx-agents":
                    assert account is not None
                    await send(f":{nick}!user@fixture JOIN :#rbx-agents")
                elif line == "MODE #rbx-agents":
                    await send(f":fixture 324 {nick} #rbx-agents +nsi")
                elif line.startswith("PRIVMSG "):
                    self.lines.append(line)
                elif line.startswith("PONG :"):
                    self.pongs.add(line.removeprefix("PONG :"))
        except (ConnectionError, asyncio.CancelledError):
            pass
        except Exception as error:
            self.errors.append(error)
        finally:
            self.tasks.discard(task)
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    async def until(self, predicate):
        async with asyncio.timeout(8):
            while not predicate():
                if self.errors:
                    raise self.errors[0]
                if self.running.done():
                    self.running.result()
                    raise AssertionError("transport stopped unexpectedly")
                await asyncio.sleep(0.01)
        assert not self.errors

    async def exchange(self, *frames):
        self.barriers += 1
        marker = f"processed-{self.barriers}"
        self.writer.write(("\r\n".join((*frames, f"PING :{marker}")) + "\r\n").encode())
        await self.writer.drain()
        await self.until(lambda: marker in self.pongs)

    async def reconnect(self):
        generation = self.connections
        self.writer.close()
        await self.writer.wait_closed()
        await self.until(lambda: self.connections > generation and self.bridge.ready)


@asynccontextmanager
async def connected_human_bridge(tmp_path):
    fixture = LoopbackIRC()
    server = await asyncio.start_server(fixture.serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    base = AgentConfig(
        "local",
        f"127.0.0.1:{port}",
        False,
        "rbx-local",
        "rbx-local",
        "RBX_AGENT_IRC_PASSWORD",
        "#rbx-agents",
        {"thinkcentre": "rbx-thinkcentre"},
        frozenset({"leandro"}),
        tmp_path / "local",
    )
    config = replace(base, human_queries=True)
    mailbox = Mailbox(config)
    fixture.bridge = AgentTransport(config, mailbox, "synthetic-fixture-password")
    fixture.running = asyncio.create_task(fixture.bridge.run())
    try:
        await fixture.until(lambda: fixture.bridge.ready)
        assert fixture.bridge.humans is not None
        assert stat.S_IMODE(config.state_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(mailbox.path.stat().st_mode) == 0o600
        yield fixture, fixture.bridge.humans
        assert not fixture.errors
    finally:
        fixture.running.cancel()
        await asyncio.gather(fixture.running, return_exceptions=True)
        server.close()
        await server.wait_closed()
        tasks = list(fixture.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert not mailbox.status()["connected"]


def open_session(humans, *, max_questions=3, now=None, duration_seconds=600):
    return humans.open_session(
        "flightdeck-dev",
        "leandro",
        "rbx-flightdeck",
        "a" * 40,
        "gpt-6.1-sol",
        max_questions=max_questions,
        duration_seconds=duration_seconds,
        now=time.time() if now is None else now,
    )


def test_socket_human_worker_reply_and_replay_after_reconnect(tmp_path):
    async def scenario():
        async with connected_human_bridge(tmp_path) as (irc, humans):
            open_session(humans)
            # A wire roundtrip puts the millisecond server timestamp after the
            # local session start; same-millisecond admission is fail-closed.
            await irc.exchange()
            original = human_frame("opaque-message-one")
            await irc.exchange(original, original)
            inbox = humans.inbox()
            assert len(inbox) == 1
            assert humans.inbox() == inbox  # Reading does not claim the request.
            request = inbox[0]
            assert request["text"] == QUESTION
            assert request["msgid"] == "opaque-message-one"
            assert request["account"] == "leandro"
            assert request["project"] == "rbx-flightdeck"
            assert request["revision"] == "a" * 40
            assert request["model"] == "gpt-6.1-sol"
            claim = humans.claim(request["id"], "documentary-fixture", lease_seconds=60)
            with pytest.raises(ValueError):
                humans.claim(request["id"], "competing-consumer", lease_seconds=60)
            with pytest.raises(ValueError):
                humans.complete(
                    request["id"], "competing-consumer", claim["fence"], ANSWER
                )
            worker_calls = [request["id"]]
            humans.complete(
                request["id"], "documentary-fixture", claim["fence"], ANSWER
            )
            await irc.until(
                lambda: humans.reference(request["id"])["delivery_status"]
                == "socket_sent"
            )
            await irc.exchange()  # The complete publication precedes this barrier.
            reply_lines = list(irc.lines)
            assert 2 <= len(reply_lines) <= 8
            pattern = re.compile(
                r"PRIVMSG #rbx-agents :\[flightdeck-dev \| Codex \| consulta documental\] "
                r"leandro: \((\d+)/(\d+)\) (.*?) \[ref (FD-[A-Za-z0-9-]+)\]$"
            )
            parts = [pattern.fullmatch(line) for line in reply_lines]
            assert all(parts)
            assert [int(part[1]) for part in parts] == list(range(1, len(parts) + 1))
            assert {int(part[2]) for part in parts} == {len(parts)}
            assert {part[4] for part in parts} == {request["reference"]}
            assert humans.reference(request["reference"])["id"] == request["id"]
            assert "".join(part[3] for part in parts) == ANSWER.strip()
            assert all(len(line.encode("utf-8")) <= 510 for line in reply_lines)
            assert all(part[3].endswith(" ") for part in parts[:-1])
            assert not any(
                "!ask" in line or "!reply" in line or request["id"] in line
                for line in reply_lines
            )
            assert humans.inbox() == []

            await irc.reconnect()
            await irc.exchange(original, original)
            assert humans.inbox() == []
            assert irc.lines == reply_lines
            with pytest.raises(ValueError):
                humans.claim(request["id"], "documentary-fixture", lease_seconds=60)
            assert worker_calls == [request["id"]]

            # A new server ID makes identical text an intentional new request.
            await irc.exchange(human_frame("opaque-message-two"))
            second = humans.inbox()
            assert len(second) == 1 and second[0]["id"] != request["id"]
            assert (
                irc.bridge.mailbox.inbox() == []
            )  # Human queries never become peer frames.

    asyncio.run(scenario())


def test_socket_admission_rejects_untrusted_history_and_inactive_limits(tmp_path):
    async def scenario():
        async with connected_human_bridge(tmp_path) as (irc, humans):
            await irc.exchange(human_frame("before-session"))
            assert humans.inbox() == []
            opened = open_session(humans, max_questions=1)
            await irc.exchange()
            now = time.time()
            await irc.exchange(
                human_frame("dm", channel="rbx-local"),
                human_frame("wrong-channel", channel="#other"),
                human_frame("unknown", account="unknown"),
                human_frame("peer", account="rbx-thinkcentre"),
                human_frame("self", nick="rbx-local"),
                human_frame("history", extra_tags=";batch=history"),
                human_frame("expired", timestamp=server_time(now - 601)),
                human_frame("future", timestamp=server_time(now + 61)),
                "@account=leandro :human!user@fixture PRIVMSG #rbx-agents :@flightdeck-dev sem metadados",
            )
            assert humans.inbox() == []
            assert irc.lines == []
            await irc.exchange(human_frame("accepted-one"), human_frame("over-limit"))
            assert len(humans.inbox()) == 1
            humans.close_session(opened["id"])
            await irc.exchange(human_frame("after-stop"))
            assert humans.inbox() == []
            open_session(humans, now=time.time() - 2, duration_seconds=1)
            await irc.exchange(human_frame("expired-session"))
            assert humans.inbox() == []
            assert irc.lines == []

    asyncio.run(scenario())
