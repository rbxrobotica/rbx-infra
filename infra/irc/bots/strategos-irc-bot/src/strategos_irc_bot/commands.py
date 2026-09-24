from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class CommandError(ValueError):
    """Raised when text is not an accepted ChatOps command."""


class CommandKind(str, Enum):
    HELP = "help"
    STATUS = "status"
    MISSION_LIST = "mission.list"
    MISSION_STATUS = "mission.status"
    MISSION_APPROVE = "mission.approve"
    DEPLOY_STATUS = "deploy.status"
    COST_TODAY = "cost.today"
    RISK_OPEN = "risk.open"


@dataclass(frozen=True)
class ParsedCommand:
    kind: CommandKind
    argument: str | None = None


MISSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def parse_command(text: str) -> ParsedCommand:
    candidate = text.strip()
    if not candidate or candidate == "!":
        raise CommandError("empty command")
    if not candidate.startswith("!"):
        raise CommandError("commands must start with !")
    if any(ord(char) < 32 for char in candidate):
        raise CommandError("control characters are forbidden")

    tokens = candidate.split()
    exact = {
        ("!help",): CommandKind.HELP,
        ("!status",): CommandKind.STATUS,
        ("!mission", "list"): CommandKind.MISSION_LIST,
        ("!deploy", "status"): CommandKind.DEPLOY_STATUS,
        ("!cost", "today"): CommandKind.COST_TODAY,
        ("!risk", "open"): CommandKind.RISK_OPEN,
    }
    key = tuple(tokens)
    if key in exact:
        return ParsedCommand(exact[key])

    if len(tokens) == 3 and tokens[:2] in (
        ["!mission", "status"],
        ["!mission", "approve"],
    ):
        mission_id = tokens[2]
        if not MISSION_ID.fullmatch(mission_id):
            raise CommandError("invalid mission id")
        kind = (
            CommandKind.MISSION_STATUS
            if tokens[1] == "status"
            else CommandKind.MISSION_APPROVE
        )
        return ParsedCommand(kind, mission_id)

    raise CommandError("unknown command")


def short_help() -> str:
    return (
        "commands: !help | !status | !mission list | !mission status <id> | "
        "!deploy status | !cost today | !risk open"
    )
