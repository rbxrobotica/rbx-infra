#!/usr/bin/env python3
"""Owner launcher; desktop privilege authentication, no password in arguments."""

import argparse
import pathlib
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resilience-source", type=pathlib.Path, required=True)
    parser.add_argument("--reference", type=pathlib.Path, required=True)
    parser.add_argument("--publication", type=pathlib.Path, required=True)
    parser.add_argument("--terminal", action="store_true",
                        help="Authenticate through sudo on the operator's terminal instead of a graphical agent")
    args = parser.parse_args()
    if args.terminal:
        # The terminal owns authentication. Never accept, capture or log its password.
        subprocess.run(["sudo", "-v"], check=True)
    endpoint = subprocess.run(["pass", "show", "rbx/healthchecks/dns-pilot"],
                              capture_output=True, check=True, timeout=30).stdout.splitlines()[0]
    privilege = ["sudo", "-n"] if args.terminal else ["/usr/bin/pkexec", "--disable-internal-agent"]
    command = privilege + ["/usr/bin/python3",
               str(pathlib.Path(__file__).resolve().with_name("privileged-activate.py")),
               "--resilience-source", str(args.resilience_source.resolve()),
               "--reference", str(args.reference.resolve()),
               "--publication", str(args.publication.resolve())]
    result = subprocess.run(command, input=endpoint, check=False, timeout=300)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
