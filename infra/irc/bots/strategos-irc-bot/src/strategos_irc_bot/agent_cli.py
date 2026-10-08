from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path

from .agent_config import load_agent_config
from .agent_store import Mailbox
from .agent_transport import AgentTransport, ProtocolError


def main() -> None:
    parser = argparse.ArgumentParser(description="RBX private agent IRC mailbox")
    parser.add_argument("--config", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("connect", help="maintain the authenticated IRC connection")
    subparsers.add_parser("mcp", help="expose the local mailbox over MCP stdio")
    subparsers.add_parser("status")
    inbox = subparsers.add_parser("inbox")
    inbox.add_argument("--limit", type=int, default=20)
    send = subparsers.add_parser("send")
    send.add_argument("peer")
    send.add_argument("text")
    reply = subparsers.add_parser("reply")
    reply.add_argument("request_id")
    reply.add_argument("text")
    ack = subparsers.add_parser("ack")
    ack.add_argument("request_id")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    os.umask(0o077)
    try:
        config = load_agent_config(args.config)
        mailbox = Mailbox(config)
        if args.command == "connect":
            password = os.environ.get(config.password_env)
            if not password:
                raise ValueError(
                    "configured IRC password environment variable is unset"
                )
            asyncio.run(AgentTransport(config, mailbox, password).run())
            return
        if args.command == "mcp":
            from .agent_mcp import serve

            serve(mailbox)
            return
        if args.command == "status":
            result = mailbox.status()
        elif args.command == "inbox":
            result = mailbox.inbox(args.limit)
        elif args.command == "send":
            result = {
                "request_id": mailbox.send(args.peer, args.text),
                "status": "queued",
            }
        elif args.command == "reply":
            mailbox.reply(args.request_id, args.text)
            result = {"request_id": args.request_id, "status": "queued"}
        else:
            mailbox.acknowledge(args.request_id)
            result = {"request_id": args.request_id, "status": "acknowledged"}
        print(json.dumps(result, ensure_ascii=False))
    except (ValueError, OSError, ProtocolError) as error:
        # Errors never contain incoming content or the IRC password.
        parser.exit(1, f"{error}\n")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
