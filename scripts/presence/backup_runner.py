#!/usr/bin/env python3
"""Public Presence-only entry point for the existing PostgreSQL archive runner.

Captures public_presence in a read-only transaction and uploads only encrypted
bytes below public-presence/backups/. Assets require their separate preservation
plan. An upload receipt is not restore or independent-key-custody evidence.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from comms import backup_runner as shared  # noqa: E402


def main():
    return shared.main(scope="public-presence")


if __name__ == "__main__":
    sys.exit(main())
