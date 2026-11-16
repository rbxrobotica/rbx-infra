#!/usr/bin/env python3
"""Private FlightDeck archive entry point. No plaintext uploads or deletion."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from plausible.s3_archive import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(scope="flightdeck"))
