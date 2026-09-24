from __future__ import annotations

import base64
import logging
import socket
import ssl
from dataclasses import dataclass
from typing import TextIO

from .auth import AuthorizationPolicy
from .client import StrategosClient
from .commands import CommandError, CommandKind, parse_command, short_help
from .config import IrcConfig

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class IrcMessage:
    tags: dict[str, str | None]
    prefix: str | None
    command: str
    params: tuple[str, ...]
    trailing: str | None


def _unescape_tag(value: str) -> str:
    replacements = {":": ";", "s": " ", "r": "\r", "n": "\n", "\\": "\\"}
    result: list[str] = []
    index = 0
    while index < len(value):
        if value[index] == "\\" and index + 1 < len(value):
            index += 1
            result.append(replacements.get(value[index], value[index]))
        else:
            result.append(value[index])
        index += 1
    return "".join(result)


def parse_irc_line(line: str) -> IrcMessage:
    rest = line.rstrip("\r\n")
    tags: dict[str, str | None] = {}
    prefix: str | None = None
    trailing: str | None = None

    if rest.startswith("@"):
        raw_tags, rest = rest[1:].split(" ", 1)
        for raw_tag in raw_tags.split(";"):
            key, separator, value = raw_tag.partition("=")
            tags[key] = _unescape_tag(value) if separator else None
    if rest.startswith(":"):
        prefix, rest = rest[1:].split(" ", 1)
    if " :" in rest:
        rest, trailing = rest.split(" :", 1)
    pieces = rest.split()
    if not pieces:
        raise ValueError("empty IRC line")
    return IrcMessage(tags, prefix, pieces[0].upper(), tuple(pieces[1:]), trailing)


class CommandDispatcher:
    def __init__(self, policy: AuthorizationPolicy, client: StrategosClient) -> None:
        self.policy = policy
        self.client = client

    def handle(
        self, text: str, account: str | None, *, is_private: bool = False
    ) -> str | None:
        if is_private:
            return None
        if not self.policy.is_allowed(account):
            return "unauthorized: authenticated account is not allowed"
        try:
            command = parse_command(text)
        except CommandError:
            return short_help()

        if command.kind is CommandKind.HELP:
            return short_help()
        if command.kind is CommandKind.STATUS:
            return self.client.status()
        if command.kind is CommandKind.MISSION_LIST:
            return self.client.mission_list()
        if command.kind is CommandKind.MISSION_STATUS:
            assert command.argument is not None
            return self.client.mission_status(command.argument)
        if command.kind is CommandKind.DEPLOY_STATUS:
            return self.client.deploy_status()
        if command.kind is CommandKind.COST_TODAY:
            return self.client.cost_today()
        if command.kind is CommandKind.RISK_OPEN:
            return self.client.risk_open()
        if command.kind is CommandKind.MISSION_APPROVE:
            if self.policy.read_only:
                return "blocked: approval is disabled in read-only mode"
            return "blocked: approval stub has no policy/RBAC/ledger integration"
        return short_help()


def _split_server(server: str) -> tuple[str, int]:
    if server.startswith("["):
        host, separator, port = server[1:].partition("]:")
    else:
        host, separator, port = server.rpartition(":")
    if not separator or not host or not port.isdigit():
        raise ValueError("irc.server must be host:port or [ipv6]:port")
    port_number = int(port)
    if not 1 <= port_number <= 65535:
        raise ValueError("IRC port is out of range")
    return host, port_number


class IrcBot:
    def __init__(
        self,
        config: IrcConfig,
        password: str,
        dispatcher: CommandDispatcher,
    ) -> None:
        self.config = config
        self.password = password
        self.dispatcher = dispatcher
        self._writer: TextIO | None = None
        self._joined = False
        self._sasl_started = False
        self._capabilities: set[str] = set()

    def _send(self, line: str) -> None:
        if self._writer is None:
            raise RuntimeError("IRC connection is not open")
        if "\r" in line or "\n" in line or "\0" in line:
            raise ValueError("invalid IRC output")
        self._writer.write(f"{line[:510]}\r\n")
        self._writer.flush()

    def _authenticate(self) -> None:
        payload = base64.b64encode(
            f"\0{self.config.username}\0{self.password}".encode()
        ).decode("ascii")
        for offset in range(0, len(payload), 400):
            self._send(f"AUTHENTICATE {payload[offset : offset + 400]}")
        if len(payload) % 400 == 0:
            self._send("AUTHENTICATE +")

    def _join(self) -> None:
        if self._joined:
            return
        for channel in self.config.channels:
            self._send(f"JOIN {channel}")
        self._joined = True

    def _handle_message(self, message: IrcMessage) -> None:
        if message.command == "PING":
            token = message.trailing or (message.params[0] if message.params else "")
            self._send(f"PONG :{token}")
            return
        if message.command == "CAP" and len(message.params) >= 2:
            subcommand = message.params[1].upper()
            capabilities = {
                capability.split("=", 1)[0]
                for capability in (message.trailing or "").split()
            }
            if subcommand == "LS":
                self._capabilities.update(capabilities)
                if "*" in message.params[2:]:
                    return
                needed = {"account-tag", "sasl"}
                if not needed.issubset(self._capabilities):
                    raise RuntimeError("server must support IRCv3 account-tag and SASL")
                self._send("CAP REQ :account-tag sasl")
            elif (
                subcommand == "ACK"
                and "sasl" in capabilities
                and not self._sasl_started
            ):
                self._sasl_started = True
                self._send("AUTHENTICATE PLAIN")
            elif subcommand == "NAK":
                raise RuntimeError("server rejected required IRCv3 capabilities")
            return
        if message.command == "AUTHENTICATE" and message.params == ("+",):
            self._authenticate()
            return
        if message.command == "903":
            self._send("CAP END")
            return
        if message.command in {"904", "905", "906", "907"}:
            raise RuntimeError("SASL authentication failed")
        if message.command == "001":
            self._join()
            return
        if (
            message.command != "PRIVMSG"
            or not message.params
            or message.trailing is None
        ):
            return

        target = message.params[0]
        is_private = not target.startswith("#")
        response = self.dispatcher.handle(
            message.trailing,
            message.tags.get("account"),
            is_private=is_private,
        )
        if response is not None:
            safe = (
                response.replace("\r", " ").replace("\n", " ").replace("\0", " ")[:350]
            )
            self._send(f"PRIVMSG {target} :{safe}")

    def run(self) -> None:
        host, port = _split_server(self.config.server)
        if not self.config.tls and host not in {"127.0.0.1", "localhost", "::1"}:
            raise RuntimeError("plaintext IRC is allowed only over loopback/tunnel")
        raw_socket = socket.create_connection((host, port), timeout=15)
        if self.config.tls:
            context = ssl.create_default_context()
            if not self.config.tls_verify:
                LOGGER.warning("TLS certificate verification is disabled")
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            connection = context.wrap_socket(raw_socket, server_hostname=host)
        else:
            connection = raw_socket

        LOGGER.info("connected to configured IRC server")
        with connection:
            reader = connection.makefile(
                "r", encoding="utf-8", errors="replace", newline="\n"
            )
            self._writer = connection.makefile("w", encoding="utf-8", newline="")
            with reader, self._writer:
                self._send("CAP LS 302")
                self._send(f"NICK {self.config.nick}")
                self._send(f"USER {self.config.username} 0 * :{self.config.realname}")
                for line in reader:
                    self._handle_message(parse_irc_line(line))
