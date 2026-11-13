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
    args = parser.parse_args()
    endpoint = subprocess.run(["pass", "show", "rbx/healthchecks/dns-pilot"],
                              capture_output=True, check=True, timeout=30).stdout.splitlines()[0]
    command = ["/usr/bin/pkexec", "--disable-internal-agent", "/usr/bin/python3",
               str(pathlib.Path(__file__).resolve().with_name("privileged-activate.py")),
               "--resilience-source", str(args.resilience_source.resolve()),
               "--reference", str(args.reference.resolve()),
               "--publication", str(args.publication.resolve())]
    result = subprocess.run(command, input=endpoint, check=False, timeout=300)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
