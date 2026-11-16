#!/usr/bin/env python3
"""FlightDeck-only, read-only PostgreSQL snapshot and encrypted versioned archive."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from comms.backup_runner import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(scope="flightdeck"))
