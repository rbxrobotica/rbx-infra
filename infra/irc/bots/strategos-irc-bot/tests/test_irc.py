import io
from typing import Self

from strategos_irc_bot.auth import AuthorizationPolicy
from strategos_irc_bot.bot import CommandDispatcher, IrcBot, parse_irc_line
from strategos_irc_bot.client import MockStrategosClient
from strategos_irc_bot.config import IrcConfig


def test_account_tag_is_parsed() -> None:
    message = parse_irc_line(
        "@account=leandro :nick!~u@rbx PRIVMSG #strategos :!status\r\n"
    )
    assert message.tags["account"] == "leandro"
    assert message.command == "PRIVMSG"
    assert message.params == ("#strategos",)
    assert message.trailing == "!status"


def test_multiline_cap_ls_is_accumulated_before_request() -> None:
    config = IrcConfig(
        server="127.0.0.1:6667",
        tls=False,
        tls_verify=True,
        nick="strategos-bot",
        username="strategos-bot",
        realname="RBX Strategos ChatOps Bot",
        channels=("#strategos",),
        password_env="STRATEGOS_IRC_PASSWORD",
    )
    dispatcher = CommandDispatcher(
        AuthorizationPolicy.from_accounts(["leandro"]), MockStrategosClient(), "rbx"
    )
    bot = IrcBot(config, "test-password", dispatcher)
    output = io.StringIO()
    bot._writer = output

    bot._handle_message(
        parse_irc_line(":irc.internal.rbx CAP * LS * :account-tag away-notify")
    )
    assert output.getvalue() == ""

    bot._handle_message(
        parse_irc_line(
            ":irc.internal.rbx CAP * LS :batch sasl=PLAIN,EXTERNAL server-time"
        )
    )
    assert output.getvalue() == "CAP REQ :account-tag sasl\r\n"


def test_messages_outside_configured_channels_are_ignored() -> None:
    config = IrcConfig(
        server="127.0.0.1:6667",
        tls=False,
        tls_verify=True,
        nick="strategos-bot",
        username="strategos-bot",
        realname="RBX Strategos ChatOps Bot",
        channels=("#strategos",),
        password_env="STRATEGOS_IRC_PASSWORD",
    )
    dispatcher = CommandDispatcher(
        AuthorizationPolicy.from_accounts(["leandro"]), MockStrategosClient(), "rbx"
    )
    bot = IrcBot(config, "test-password", dispatcher)
    output = io.StringIO()
    bot._writer = output

    bot._handle_message(
        parse_irc_line("@account=leandro :leandro!~u@rbx PRIVMSG #incidents :!status")
    )

    assert output.getvalue() == ""


def test_connect_timeout_is_removed_from_established_session(monkeypatch) -> None:
    class FakeSocket:
        def __init__(self) -> None:
            self.timeouts: list[float | None] = []
            self.reader = io.StringIO("")
            self.writer = io.StringIO()

        def settimeout(self, timeout: float | None) -> None:
            self.timeouts.append(timeout)

        def makefile(self, mode: str, **_kwargs: object) -> io.StringIO:
            return self.reader if mode == "r" else self.writer

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    config = IrcConfig(
        server="127.0.0.1:6667",
        tls=False,
        tls_verify=True,
        nick="strategos-bot",
        username="strategos-bot",
        realname="RBX Strategos ChatOps Bot",
        channels=("#strategos",),
        password_env="STRATEGOS_IRC_PASSWORD",
    )
    dispatcher = CommandDispatcher(
        AuthorizationPolicy.from_accounts(["leandro"]), MockStrategosClient(), "rbx"
    )
    fake_socket = FakeSocket()

    def create_connection(address: tuple[str, int], timeout: float) -> FakeSocket:
        assert address == ("127.0.0.1", 6667)
        assert timeout == 15
        return fake_socket

    monkeypatch.setattr(
        "strategos_irc_bot.bot.socket.create_connection", create_connection
    )

    IrcBot(config, "test-password", dispatcher).run()

    assert fake_socket.timeouts == [None]
