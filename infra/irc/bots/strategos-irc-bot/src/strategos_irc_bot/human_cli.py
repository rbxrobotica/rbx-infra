"""Local operator commands for the supervised human-query mailbox.

These commands manage persisted sessions and claims. They do not connect to
IRC, launch a worker, or invoke a model. Session admission is deliberately not
exposed through the peer MCP tools.
"""

from __future__ import annotations

import argparse

from .agent_store import Mailbox


def _bounded_integer(low: int, high: int):
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError("expected an integer") from None
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"must be between {low} and {high}")
        return number

    return parse


def add_human_parser(subparsers: argparse._SubParsersAction) -> None:
    human = subparsers.add_parser(
        "human",
        help="manage supervised human queries locally (requires human_queries)",
        description=(
            "Trusted local operator session and claim management. These commands "
            "do not connect to IRC, invoke models, or start automatic workers."
        ),
    )
    commands = human.add_subparsers(dest="human_command", required=True)
    opening = commands.add_parser("session-open", help="open a bounded session")
    opening.add_argument(
        "--alias",
        default="flightdeck-dev",
        help="fixed alias: flightdeck-dev",
    )
    opening.add_argument("--account", required=True, help="allowed human SASL account")
    opening.add_argument(
        "--project", required=True, help="context project: rbx-flightdeck"
    )
    opening.add_argument(
        "--revision",
        required=True,
        help="full context revision (40 lowercase hex digits)",
    )
    opening.add_argument(
        "--model",
        required=True,
        help="explicit model metadata; no model is invoked",
    )
    opening.add_argument(
        "--duration-seconds", type=_bounded_integer(1, 600), default=600
    )
    opening.add_argument("--max-questions", type=_bounded_integer(1, 3), default=3)
    closing = commands.add_parser(
        "session-close",
        help="stop admission and pending work",
    )
    closing.add_argument("session_id")
    commands.add_parser("status", help="show session and request state")
    inbox = commands.add_parser(
        "inbox",
        help="read queued requests without claiming them",
    )
    inbox.add_argument("--limit", type=_bounded_integer(1, 100), default=20)
    claim = commands.add_parser(
        "claim",
        help="claim a request for a supervised consumer",
    )
    claim.add_argument("request_id")
    claim.add_argument("--owner", required=True)
    claim.add_argument("--lease-seconds", type=_bounded_integer(1, 600), default=60)
    complete = commands.add_parser(
        "complete",
        help="persist and queue the claim holder's answer",
    )
    complete.add_argument("request_id")
    complete.add_argument("text")
    complete.add_argument("--owner", required=True)
    complete.add_argument("--fence", type=_bounded_integer(1, 2**63 - 1), required=True)
    failed = commands.add_parser("fail", help="record failure or an uncertain result")
    failed.add_argument("request_id")
    failed.add_argument("reason")
    failed.add_argument("--owner", required=True)
    failed.add_argument("--fence", type=_bounded_integer(1, 2**63 - 1), required=True)
    failed.add_argument("--uncertain", action="store_true")
    reference = commands.add_parser(
        "reference",
        help="inspect a short reference and its metadata",
    )
    reference.add_argument("reference")


def run_human_command(mailbox: Mailbox, args: argparse.Namespace) -> dict | list:
    # Admission can only be enabled in the trusted local configuration. A CLI
    # flag or an IRC message cannot opt a disabled installation into this path.
    if not mailbox.config.human_queries:
        raise ValueError("human queries are disabled in the local configuration")

    from .human_store import HumanMailbox

    human = HumanMailbox(mailbox)
    if args.human_command == "session-open":
        return human.open_session(
            args.alias,
            args.account,
            args.project,
            args.revision,
            args.model,
            duration_seconds=args.duration_seconds,
            max_questions=args.max_questions,
        )
    if args.human_command == "session-close":
        return human.close_session(args.session_id)
    if args.human_command == "status":
        return human.status()
    if args.human_command == "inbox":
        return human.inbox(args.limit)
    if args.human_command == "claim":
        return human.claim(args.request_id, args.owner, args.lease_seconds)
    if args.human_command == "complete":
        return human.complete(args.request_id, args.owner, args.fence, args.text)
    if args.human_command == "fail":
        return human.fail(
            args.request_id,
            args.owner,
            args.fence,
            args.reason,
            uncertain=args.uncertain,
        )
    if args.human_command == "reference":
        return human.reference(args.reference)
    raise ValueError("unknown human command")
