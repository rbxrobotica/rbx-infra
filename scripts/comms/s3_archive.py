#!/usr/bin/env python3
"""Comms-only CLI for the shared encrypted, versioned archive transport."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from plausible import s3_archive as shared  # noqa: E402


def main(argv=None):
    return shared.main(argv, scope="comms")


if __name__ == "__main__":
    sys.exit(main())
