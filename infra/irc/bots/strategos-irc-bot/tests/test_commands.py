import pytest
from strategos_irc_bot.commands import CommandError, CommandKind, parse_command


def test_parser_recognizes_help() -> None:
    assert parse_command("!help").kind is CommandKind.HELP


def test_parser_recognizes_mission_status() -> None:
    command = parse_command("!mission status ABC")
    assert command.kind is CommandKind.MISSION_STATUS
    assert command.argument == "ABC"


@pytest.mark.parametrize("text", ["", "   ", "!"])
def test_parser_rejects_empty_command(text: str) -> None:
    with pytest.raises(CommandError):
        parse_command(text)


@pytest.mark.parametrize(
    "text",
    [
        "!mission status ABC; rm -rf /",
        "!mission status $(id)",
        "!mission status ABC|whoami",
        "!status && reboot",
    ],
)
def test_parser_rejects_shell_injection_as_operational_command(text: str) -> None:
    with pytest.raises(CommandError):
        parse_command(text)
