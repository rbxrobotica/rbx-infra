import io

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
        AuthorizationPolicy.from_accounts(["leandro"]), MockStrategosClient()
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
