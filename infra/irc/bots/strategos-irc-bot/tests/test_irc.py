from strategos_irc_bot.bot import parse_irc_line


def test_account_tag_is_parsed() -> None:
    message = parse_irc_line(
        "@account=leandro :nick!~u@rbx PRIVMSG #strategos :!status\r\n"
    )
    assert message.tags["account"] == "leandro"
    assert message.command == "PRIVMSG"
    assert message.params == ("#strategos",)
    assert message.trailing == "!status"
