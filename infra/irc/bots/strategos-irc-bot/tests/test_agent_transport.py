import asyncio
import base64
import time
from dataclasses import replace

import pytest

from strategos_irc_bot.agent_config import AgentConfig
from strategos_irc_bot.agent_protocol import Envelope, render
from strategos_irc_bot.agent_store import Mailbox
from strategos_irc_bot.agent_transport import AgentTransport, ProtocolError
from strategos_irc_bot.bot import parse_irc_line


class Writer:
    def __init__(self):
        self.lines = []

    def write(self, value):
        self.lines.append(value.decode().rstrip("\r\n"))

    async def drain(self):
        pass


def config_for(tmp_path, agent_id="local"):
    peer = "thinkcentre" if agent_id == "local" else "local"
    return AgentConfig(
        agent_id,
        "127.0.0.1:6667",
        False,
        "rbx-" + agent_id,
        "rbx-" + agent_id,
        "RBX_AGENT_IRC_PASSWORD",
        "#rbx-agents",
        {peer: "rbx-" + peer},
        frozenset({"leandro"}),
        tmp_path / agent_id,
    )


def test_multiline_caps_split_ack_auth_then_private_channel(tmp_path):
    async def scenario():
        config = config_for(tmp_path)
        bot = AgentTransport(config, Mailbox(config), "secret-fixture")
        bot.writer = Writer()

        async def handle(line):
            await bot.handle(parse_irc_line(line))

        await handle(":server CAP * LS * :account-tag")
        assert bot.writer.lines == []
        await handle(":server CAP * LS :sasl=PLAIN,EXTERNAL")
        assert bot.writer.lines == ["CAP REQ :account-tag sasl"]
        await handle(":server CAP * ACK :sasl")
        assert not bot.sasl_started
        await handle(":server CAP * ACK :account-tag")
        assert bot.writer.lines[-1] == "AUTHENTICATE PLAIN"
        await handle("AUTHENTICATE +")
        await handle(":server 903 rbx-local :success")
        await handle(":server 001 rbx-local :welcome")
        assert bot.writer.lines[-1] == "JOIN #rbx-agents"
        assert not bot.ready
        await handle(":rbx-local!u@h JOIN :#rbx-agents")
        assert bot.writer.lines[-1] == "MODE #rbx-agents"
        await handle(":server 324 rbx-local #rbx-agents +nsi")
        assert bot.ready
        assert bot.mailbox.status()["connected"]
        await handle("PING :fixture")
        assert bot.writer.lines[-1] == "PONG :fixture"
        with pytest.raises(ProtocolError, match="privacy"):
            await handle(":operator MODE #rbx-agents -s")

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "line",
    [
        ":server CAP * LS :sasl",
        ":server CAP * NAK :sasl",
        ":server 001 rbx-local :welcome",
        ":server 904 rbx-local :password invalid",
        ":server 324 rbx-local #rbx-agents +ns",
        ":server 473 rbx-local #rbx-agents :invite only",
    ],
)
def test_fail_closed_before_any_application_io(tmp_path, line):
    async def scenario():
        config = config_for(tmp_path)
        bot = AgentTransport(config, Mailbox(config), "fixture")
        bot.writer = Writer()
        with pytest.raises(ProtocolError):
            await bot.handle(parse_irc_line(line))
        assert not bot.ready
        assert bot.mailbox.inbox() == []

    asyncio.run(scenario())


def test_replay_dm_ambient_unknown_and_unready_messages_are_ignored(tmp_path):
    async def scenario():
        config = config_for(tmp_path)
        bot = AgentTransport(config, Mailbox(config), "fixture")
        bot.writer = Writer()
        text = render(Envelope("ask", "local", "a" * 32, int(time.time()), "status?"))

        def frame(tags="account=leandro", channel="#rbx-agents", body=text):
            return parse_irc_line(f"@{tags} :human!u@h PRIVMSG {channel} :{body}")

        await bot.handle(frame())
        assert bot.mailbox.inbox() == []
        bot.ready = True
        for message in [
            frame(channel="rbx-local"),
            frame(channel="#other"),
            frame(tags="account=unknown"),
            frame(tags="account=leandro;batch=history"),
            frame(tags="time=2026-10-07T00:00:00Z"),
            frame(body="conversa ambiente"),
            frame(body="!reply local " + "b" * 32 + " 12 1/1 ok"),
        ]:
            await bot.handle(message)
        assert bot.mailbox.inbox() == []
        assert bot.writer.lines == []
        await bot.handle(frame())
        assert len(bot.mailbox.inbox()) == 1
        await bot.handle(frame())
        assert len(bot.mailbox.inbox()) == 1

    asyncio.run(scenario())


def test_real_sockets_two_authenticated_bridges_roundtrip(tmp_path):
    """A protocol fixture, not a claim of live Ergo/host acceptance."""

    async def scenario():
        clients = {}
        tasks = set()
        errors = []

        async def serve(reader, writer):
            task = asyncio.current_task()
            tasks.add(task)
            nick = account = None
            try:

                async def send(line):
                    writer.write((line + "\r\n").encode())
                    await writer.drain()

                while raw := await reader.readline():
                    line = raw.decode().rstrip("\r\n")
                    if line == "CAP LS 302":
                        await send(":fixture CAP * LS * :account-tag")
                        await send(":fixture CAP * LS :sasl=PLAIN")
                    elif line.startswith("NICK "):
                        nick = line.split()[1]
                    elif line == "CAP REQ :account-tag sasl":
                        await send(f":fixture CAP {nick} ACK :account-tag sasl")
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
                        clients[nick] = (account, writer)
                        await send(f":{nick}!user@fixture JOIN :#rbx-agents")
                    elif line == "MODE #rbx-agents":
                        await send(f":fixture 324 {nick} #rbx-agents +nsi")
                    elif line.startswith("PRIVMSG #rbx-agents :"):
                        body = line.partition(" :")[2]
                        for peer, (_, destination) in list(clients.items()):
                            if peer != nick:
                                destination.write(
                                    f"@account={account} :{nick}!user@fixture PRIVMSG #rbx-agents :{body}\r\n".encode()
                                )
                                await destination.drain()
            except (ConnectionError, asyncio.CancelledError):
                pass
            except Exception as error:
                errors.append(error)
            finally:
                clients.pop(nick, None)
                tasks.discard(task)
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        local_config = replace(config_for(tmp_path), server=f"127.0.0.1:{port}")
        remote_config = replace(
            config_for(tmp_path, "thinkcentre"), server=f"127.0.0.1:{port}"
        )
        local, remote = Mailbox(local_config), Mailbox(remote_config)
        bridges = [
            AgentTransport(local_config, local, "fixture-password"),
            AgentTransport(remote_config, remote, "fixture-password"),
        ]
        running = [asyncio.create_task(bridge.session()) for bridge in bridges]

        async def until(predicate):
            async with asyncio.timeout(8):
                while not predicate():
                    for task in running:
                        if task.done():
                            task.result()
                    await asyncio.sleep(0.05)

        try:
            await until(lambda: all(bridge.ready for bridge in bridges))
            request_id = local.send("thinkcentre", "Como está o backup?")
            await until(lambda: bool(remote.inbox()))
            assert remote.inbox()[0]["id"] == request_id
            remote.reply(request_id, "Fixture: backup conferido.")
            await until(lambda: bool(local.inbox()))
            assert local.inbox()[0]["response"] == "Fixture: backup conferido."
            assert remote.inbox() == []
            assert not errors
        finally:
            for task in running:
                task.cancel()
            await asyncio.gather(*running, return_exceptions=True)
            for task in list(tasks):
                task.cancel()
            await asyncio.gather(*list(tasks), return_exceptions=True)
            server.close()
            await server.wait_closed()
        assert not local.status()["connected"]
        assert not remote.status()["connected"]

    asyncio.run(scenario())
