"""IRC transport only: authenticated mailbox I/O, no shell, model or tool calls."""

from __future__ import annotations

import asyncio
import base64
import fcntl
import logging
import os
import ssl
import time

from .agent_config import AgentConfig
from .agent_protocol import parse_envelope
from .agent_store import Mailbox
from .human_protocol import HUMAN_CAPABILITIES, message_metadata, parse_human_query
from .human_store import HumanMailbox
from .mailbox_database import MailboxStorageError
from .bot import IrcMessage, _split_server, parse_irc_line

LOGGER = logging.getLogger(__name__)


class ProtocolError(RuntimeError):
    """Fail closed on authentication or channel authorization failure."""


class AgentTransport:
    def __init__(self, config: AgentConfig, mailbox: Mailbox, password: str) -> None:
        self.config, self.mailbox, self.password = config, mailbox, password
        self.writer: asyncio.StreamWriter | None = None
        self.ready = False
        self.authenticated = False
        self.capabilities: set[str] = set()
        self.acked: set[str] = set()
        self.sasl_started = False
        self.modes: set[str] = set()
        self.humans = HumanMailbox(mailbox) if config.human_queries else None

    @property
    def required_capabilities(self) -> frozenset[str]:
        return HUMAN_CAPABILITIES if self.humans else frozenset({"account-tag", "sasl"})

    async def send_line(self, line: str) -> None:
        if any(c in line for c in "\r\n\0") or len(line.encode("utf-8")) > 510:
            raise ValueError("invalid or oversized IRC frame")
        assert self.writer is not None
        self.writer.write((line + "\r\n").encode("utf-8"))
        # Python 3.11 wait_for can consume external cancellation when its child
        # completes concurrently. Keep I/O in this task so shutdown propagates.
        async with asyncio.timeout(10):
            await self.writer.drain()

    async def handle(self, message: IrcMessage) -> None:
        command = message.command
        if command == "PING":
            await self.send_line(
                "PONG :"
                + (message.trailing or (message.params[0] if message.params else ""))
            )
        elif command == "CAP" and len(message.params) >= 2:
            subcommand = message.params[1].upper()
            capabilities = {
                v.split("=", 1)[0] for v in (message.trailing or "").split()
            }
            if subcommand == "LS":
                self.capabilities.update(capabilities)
                if "*" in message.params[2:]:
                    return
                if not self.required_capabilities <= self.capabilities:
                    raise ProtocolError("required IRCv3 capabilities are absent")
                request = "account-tag sasl"
                if self.humans:
                    request += " message-tags server-time"
                await self.send_line("CAP REQ :" + request)
            elif subcommand == "ACK":
                self.acked.update(capabilities)
                if self.required_capabilities <= self.acked and not self.sasl_started:
                    self.sasl_started = True
                    await self.send_line("AUTHENTICATE PLAIN")
            elif subcommand in {"NAK", "DEL"}:
                raise ProtocolError(
                    "required IRCv3 capabilities were rejected or removed"
                )
        elif command == "AUTHENTICATE" and message.params == ("+",):
            if not self.sasl_started:
                raise ProtocolError("unexpected SASL challenge")
            payload = base64.b64encode(
                f"\0{self.config.account}\0{self.password}".encode()
            ).decode("ascii")
            for start in range(0, len(payload), 400):
                await self.send_line("AUTHENTICATE " + payload[start : start + 400])
            if len(payload) % 400 == 0:
                await self.send_line("AUTHENTICATE +")
        elif command == "903":
            if not self.sasl_started:
                raise ProtocolError("unexpected SASL success")
            self.authenticated = True
            await self.send_line("CAP END")
        elif command in {
            "904",
            "905",
            "906",
            "907",
            "433",
            "473",
            "474",
            "475",
            "477",
            "489",
        }:
            raise ProtocolError(
                "IRC authentication, nickname or channel admission failed"
            )
        elif command == "001":
            if not self.authenticated:
                raise ProtocolError("IRC welcome received before authentication")
            await self.send_line("JOIN " + self.config.channel)
        elif (
            command == "JOIN"
            and message.prefix
            and message.prefix.split("!", 1)[0].casefold()
            == self.config.nick.casefold()
        ):
            target = message.trailing or (message.params[0] if message.params else "")
            if target == self.config.channel:
                await self.send_line("MODE " + self.config.channel)
        elif (
            command == "324"
            and len(message.params) >= 3
            and message.params[1] == self.config.channel
        ):
            self.modes = set(message.params[2].lstrip("+"))
            if not self.authenticated or not {"s", "i"} <= self.modes:
                raise ProtocolError(
                    "pilot channel must be secret and invite-only (+si)"
                )
            self.ready = True
            self.mailbox.heartbeat(True)
        elif (
            command == "MODE"
            and len(message.params) >= 2
            and message.params[0] == self.config.channel
        ):
            adding = True
            for mode in message.params[1]:
                if mode in "+-":
                    adding = mode == "+"
                elif adding:
                    self.modes.add(mode)
                else:
                    self.modes.discard(mode)
            if self.ready and not {"s", "i"} <= self.modes:
                raise ProtocolError("pilot channel privacy modes were removed")
        elif (
            command == "KICK"
            and len(message.params) >= 2
            and message.params[:2] == (self.config.channel, self.config.nick)
        ):
            raise ProtocolError("agent was removed from pilot channel")
        elif command == "ERROR":
            raise ConnectionError("IRC connection closed by server")
        elif (
            command == "PRIVMSG"
            and self.ready
            and message.params == (self.config.channel,)
            and message.trailing
        ):
            # No history capability is negotiated. Reject all batched/replayed
            # traffic as well; payload timestamps provide a second expiry fence.
            account = message.tags.get("account")
            if not account or "batch" in message.tags:
                return
            try:
                if message.trailing.startswith("@"):
                    if (
                        not self.humans
                        or not self.authenticated
                        or not HUMAN_CAPABILITIES <= self.acked
                        or not message.prefix
                        or message.prefix.split("!", 1)[0].casefold()
                        == self.config.nick.casefold()
                    ):
                        return
                    alias, question = parse_human_query(message.trailing)
                    msgid, server_time = message_metadata(message.tags)
                    self.humans.receive(alias, account, msgid, server_time, question)
                    return
                envelope = parse_envelope(message.trailing)
                self.mailbox.receive(envelope, account)
            except MailboxStorageError:
                raise
            except ValueError:
                return  # Never echo rejected content or identities into the room.

    async def publish_human_reply(self) -> bool:
        if (
            not self.humans
            or not self.ready
            or not self.authenticated
            or not HUMAN_CAPABILITIES <= self.acked
        ):
            return False

        def write(lines: list[str]) -> None:
            # The store's transaction fences this synchronous write against a
            # concurrent session stop. Never await while holding a database lock.
            assert self.writer is not None
            for body in lines:
                line = f"PRIVMSG {self.config.channel} :{body}"
                if any(c in line for c in "\r\n\0") or len(line.encode("utf-8")) > 510:
                    raise ValueError("invalid human reply frame")
                self.writer.write((line + "\r\n").encode("utf-8"))

        attempted = self.humans.dispatch_pending(write)
        if attempted is None:
            return False
        assert self.writer is not None
        async with asyncio.timeout(10):
            await self.writer.drain()
        self.humans.mark_socket_sent(attempted["id"])
        return True

    async def session(self) -> None:
        self.ready = self.authenticated = self.sasl_started = False
        self.capabilities.clear()
        self.acked.clear()
        self.modes.clear()
        host, port = _split_server(self.config.server)
        context = ssl.create_default_context() if self.config.tls else None
        async with asyncio.timeout(15):
            reader, self.writer = await asyncio.open_connection(
                host, port, ssl=context, limit=8192
            )
        started = last_outgoing = last_heartbeat = time.monotonic()
        try:
            await self.send_line("CAP LS 302")
            await self.send_line("NICK " + self.config.nick)
            await self.send_line(f"USER {self.config.account} 0 * :RBX agent mailbox")
            while True:
                now = time.monotonic()
                if not self.ready and now - started > 20:
                    raise ProtocolError("IRC authentication/channel check timed out")
                if self.ready:
                    if now - last_heartbeat >= 2:
                        self.mailbox.heartbeat(True)
                        last_heartbeat = now
                    if now - last_outgoing >= 1:
                        # Bound idle database scans as well as socket writes.
                        last_outgoing = now
                        human_sent = await self.publish_human_reply()
                        queued = (
                            self.mailbox.next_outgoing() if not human_sent else None
                        )
                        if queued:
                            row_id, text = queued
                            await self.send_line(
                                f"PRIVMSG {self.config.channel} :{text}"
                            )
                            # This means written to the socket, not peer delivery.
                            # Correlated reply is the end-to-end receipt.
                            self.mailbox.sent(row_id)
                        # Space polls after I/O completes, including slow remote
                        # transactions, rather than catching up in a burst.
                        last_outgoing = time.monotonic()
                try:
                    async with asyncio.timeout(0.25):
                        line = await reader.readline()
                except asyncio.TimeoutError:
                    continue
                if not line:
                    raise ConnectionError("IRC peer disconnected")
                try:
                    message = parse_irc_line(line.decode("utf-8", errors="strict"))
                except (ValueError, UnicodeError):
                    continue
                await self.handle(message)
        finally:
            self.ready = False
            self.writer.close()
            try:
                async with asyncio.timeout(5):
                    await self.writer.wait_closed()
            except (OSError, asyncio.TimeoutError):
                pass
            # A database outage must not leave an authenticated IRC socket open.
            self.mailbox.heartbeat(False)

    async def run(self) -> None:
        if self.config.storage_backend == "postgresql":
            # The ownership session also executes all daemon transactions. If it
            # is lost, storage errors escape without reconnecting or sending.
            with self.mailbox.database.transport_owner():
                await self._run_owned()
            return
        # SQLite ownership is limited to this machine and private state directory.
        fd = os.open(
            self.config.state_dir / "bridge.lock",
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
        )
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ProtocolError(
                    "another IRC daemon already owns this mailbox"
                ) from error
            await self._run_owned()
        finally:
            os.close(fd)

    async def _run_owned(self) -> None:
        delay = 1
        while True:
            self.mailbox.heartbeat(False)
            started = time.monotonic()
            try:
                await self.session()
            except (MailboxStorageError, ssl.SSLError, ProtocolError):
                raise
            except (OSError, ConnectionError, asyncio.TimeoutError, ValueError):
                LOGGER.warning(
                    "IRC transport disconnected; reconnecting without logging payloads"
                )
            if time.monotonic() - started > 30:
                delay = 1
            await asyncio.sleep(delay)
            delay = min(30, delay * 2)
