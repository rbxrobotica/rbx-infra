"""Real loopback IRC sockets and disposable PostgreSQL, never live services."""

import asyncio
import base64
import time
from datetime import datetime, timezone

import pytest

from strategos_irc_bot.agent_protocol import Envelope, render
from strategos_irc_bot.agent_store import Mailbox
from strategos_irc_bot.agent_transport import AgentTransport
from strategos_irc_bot.bot import parse_irc_line
from strategos_irc_bot.human_protocol import HUMAN_CAPABILITIES
from strategos_irc_bot.mailbox_database import (
    MailboxStorageError,
    initialize_postgres,
    postgres_connection,
)


@pytest.mark.parametrize("human", [False, True])
def test_postgres_failure_is_not_swallowed_as_invalid_irc_content(
    postgres_config, human
):
    initialize_postgres(postgres_config)
    mailbox = Mailbox(postgres_config)
    bot = AgentTransport(postgres_config, mailbox, "fixture-password")
    bot.ready = bot.authenticated = True
    bot.acked = set(HUMAN_CAPABILITIES)
    with postgres_connection(postgres_config) as db:
        db.execute(
            "INSERT INTO meta VALUES ('migration_hold','reconciliation_required')"
        )
    instant = (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    account = sorted(postgres_config.operators)[0]
    tags = f"account={account};msgid=fixture-request;time={instant}"
    body = (
        "@flightdeck-dev Qual é o limite?"
        if human
        else render(
            Envelope(
                "ask", postgres_config.agent_id, "a" * 32, int(time.time()), "status?"
            )
        )
    )
    message = parse_irc_line(
        f"@{tags} :human!u@fixture PRIVMSG {postgres_config.channel} :{body}"
    )
    with pytest.raises(MailboxStorageError, match="held for offline reconciliation"):
        asyncio.run(bot.handle(message))
    assert not postgres_config.state_dir.exists()


class IRCFixture:
    """Minimal authenticated/private-channel protocol fixture with visible I/O."""

    def __init__(self):
        self.connections = 0
        self.lines = []
        self.tasks = set()
        self.clients = set()
        self.errors = []
        self.eof = asyncio.Event()
        self.writer = None

    async def serve(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        self.clients.add(writer)
        self.writer = writer
        self.connections += 1
        nick = account = None
        try:

            async def send(line):
                writer.write((line + "\r\n").encode())
                await writer.drain()

            while raw := await reader.readline():
                line = raw.decode().rstrip("\r\n")
                self.lines.append(line)
                if line == "CAP LS 302":
                    await send(
                        ":fixture CAP * LS :account-tag sasl message-tags server-time"
                    )
                elif line.startswith("NICK "):
                    nick = line.split()[1]
                elif line.startswith("CAP REQ :"):
                    await send(f":fixture CAP {nick} ACK :" + line.partition(" :")[2])
                elif line == "AUTHENTICATE PLAIN":
                    await send("AUTHENTICATE +")
                elif line.startswith("AUTHENTICATE "):
                    _, account, password = (
                        base64.b64decode(line.split()[1]).decode().split("\0")
                    )
                    assert password == "fixture-password"
                    assert account == nick
                    await send(f":fixture 903 {nick} :SASL success")
                elif line == "CAP END":
                    await send(f":fixture 001 {nick} :welcome")
                elif line == "JOIN #rbx-agents":
                    assert account is not None
                    await send(f":{nick}!user@fixture JOIN :#rbx-agents")
                elif line == "MODE #rbx-agents":
                    await send(f":fixture 324 {nick} #rbx-agents +nsi")
        except (ConnectionError, asyncio.CancelledError):
            pass
        except Exception as error:
            self.errors.append(error)
        finally:
            self.eof.set()
            self.tasks.discard(task)
            self.clients.discard(writer)
            writer.close()
            await writer.wait_closed()

    async def stop(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def until(predicate, running):
    async with asyncio.timeout(8):
        while not predicate():
            if running.done():
                running.result()
            await asyncio.sleep(0.025)


def test_postgres_run_uses_shared_ownership_without_local_lockfile(
    postgres_config_factory,
):
    async def scenario():
        fixture = IRCFixture()
        server = await asyncio.start_server(fixture.serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        config = postgres_config_factory(server=f"127.0.0.1:{port}")
        initialize_postgres(config)
        bot = AgentTransport(config, Mailbox(config), "fixture-password")
        other = AgentTransport(config, Mailbox(config), "fixture-password")
        running = asyncio.create_task(bot.run())
        try:
            await until(lambda: bot.ready, running)
            assert not config.state_dir.exists()
            with pytest.raises(
                MailboxStorageError, match="transport is already active"
            ):
                await other.run()
            assert fixture.connections == 1
            request = render(
                Envelope("ask", config.agent_id, "a" * 32, int(time.time()), "status?")
            )
            fixture.writer.write(
                f"@account=leandro :human!u@fixture PRIVMSG #rbx-agents :{request}\r\n".encode()
            )
            await fixture.writer.drain()
            await until(lambda: bool(bot.mailbox.inbox()), running)
            bot.mailbox.reply("a" * 32, "Fixture response")
            await until(
                lambda: any(
                    line.startswith("PRIVMSG #rbx-agents :!reply")
                    for line in fixture.lines
                ),
                running,
            )
            assert not fixture.errors
            assert not config.state_dir.exists()
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
            await fixture.stop()
            server.close()
            await server.wait_closed()
        assert not Mailbox(config).status()["connected"]
        assert not config.state_dir.exists()

    asyncio.run(scenario())


def test_postgres_database_loss_closes_socket_without_reconnect_or_more_sends(
    postgres_config_factory,
):
    async def scenario():
        fixture = IRCFixture()
        server = await asyncio.start_server(fixture.serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        config = postgres_config_factory(server=f"127.0.0.1:{port}")
        initialize_postgres(config)
        bot = AgentTransport(config, Mailbox(config), "fixture-password")
        running = asyncio.create_task(bot.run())
        try:
            await until(lambda: bot.ready, running)
            with bot.mailbox.connection() as db:
                owner_pid = db.execute("SELECT pg_backend_pid()").fetchone()[0]
            with postgres_connection(config) as db:
                assert db.execute(
                    "SELECT pg_terminate_backend(?)", (owner_pid,)
                ).fetchone()[0]
            # A valid application frame reaches the database immediately, before
            # the periodic heartbeat or outgoing scan can hide an ingress defect.
            question = render(
                Envelope(
                    "ask",
                    config.agent_id,
                    "b" * 32,
                    int(time.time()),
                    "Must fail closed",
                )
            )
            fixture.writer.write(
                f"@account=leandro :human!u@fixture PRIVMSG #rbx-agents :{question}\r\n".encode()
            )
            await fixture.writer.drain()
            with pytest.raises(MailboxStorageError, match="outcome may be uncertain"):
                await asyncio.wait_for(running, timeout=5)
            await asyncio.wait_for(fixture.eof.wait(), timeout=2)
            assert bot.writer.is_closing()
            assert not bot.ready
            assert fixture.connections == 1
            assert not any(line.startswith("PRIVMSG ") for line in fixture.lines)
            assert not config.state_dir.exists()
            assert not fixture.errors
            # A fresh administrative reader can inspect state. The failed daemon
            # itself must not reconnect its lost database ownership connection.
            assert Mailbox(config).inbox() == []
        finally:
            if not running.done():
                running.cancel()
            await asyncio.gather(running, return_exceptions=True)
            await fixture.stop()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())
