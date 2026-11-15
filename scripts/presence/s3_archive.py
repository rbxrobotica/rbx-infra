#!/usr/bin/env python3
"""Keep Public Presence archives isolated from Comms and Plausible prefixes."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from plausible import s3_archive as shared  # noqa: E402


def main(argv=None):
    return shared.main(argv, scope="public-presence")


if __name__ == "__main__":
    sys.exit(main())
