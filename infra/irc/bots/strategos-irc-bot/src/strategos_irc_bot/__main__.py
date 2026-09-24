from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from .auth import AuthorizationPolicy
from .bot import CommandDispatcher, IrcBot
from .client import MockStrategosClient
from .config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="RBX Strategos IRC adapter")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = load_config(args.config)
    if config.strategos.enabled:
        raise SystemExit(
            "strategos.enabled=true is unsupported: a reviewed real client is not implemented"
        )
    password = os.environ.get(config.irc.password_env)
    if not password:
        raise SystemExit(
            f"required password environment variable is unset: {config.irc.password_env}"
        )

    policy = AuthorizationPolicy.from_accounts(
        list(config.auth.allowed_accounts), config.auth.read_only
    )
    dispatcher = CommandDispatcher(policy, MockStrategosClient())
    IrcBot(config.irc, password, dispatcher).run()


if __name__ == "__main__":
    main()
