#!/usr/bin/env python3
"""Read-only DNS detector with bounded, token-redacted heartbeat reporting."""

import argparse
import datetime
import importlib.util
import json
import os
import pathlib
import re
import sys
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("redirect_rejected")


def heartbeat(endpoint, failed, summary):
    if not re.fullmatch(r"https://hc-ping\.com/[0-9a-f-]{36}", endpoint):
        raise ValueError("invalid_ping_destination")
    url = endpoint + ("/fail" if failed else "")
    request = urllib.request.Request(url, data=summary.encode("ascii"), method="POST")
    with urllib.request.build_opener(NoRedirect).open(request, timeout=10) as response:
        if response.status != 200 or response.read(32).strip() != b"OK":
            raise ValueError("heartbeat_not_confirmed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True, type=pathlib.Path)
    parser.add_argument("--state-dir", required=True, type=pathlib.Path)
    parser.add_argument("--credential-stdin", action="store_true",
                        help="One-shot delivery drill only; read the per-check endpoint from stdin")
    args = parser.parse_args()
    report = {"observed_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    code = 3
    try:
        config = json.loads((args.release / "publication.json").read_text())
        if config["origin"] != "cloudns-xfr-pilot.rbxsystems.ch." or not config["synthetic_only"]:
            raise ValueError("unexpected_publication_scope")
        spec = importlib.util.spec_from_file_location("dns_health", args.release / "checker.py")
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        reference = checker.load_reference(args.release / "reference.zone",
                                           config["sha256"], config["ds_sha256"])
        report = checker.run(reference, config["warn_before_seconds"],
                             config["sha256"], config["ds_sha256"])
        code = report["exit_code"]
    except Exception as error:
        report.update({"exit_code": code, "error_type": type(error).__name__})
    try:
        if args.credential_stdin:
            endpoint = sys.stdin.buffer.read(1024).decode("ascii").strip()
        else:
            credentials = pathlib.Path(os.environ["CREDENTIALS_DIRECTORY"])
            endpoint = (credentials / "healthchecks").read_text().strip()
        heartbeat(endpoint, code != 0, f"synthetic DNS monitor exit={code}; checks={report.get('checks', 0)}")
        report["remote_signal_confirmed"] = True
    except Exception as error:
        report.update({"remote_signal_confirmed": False,
                       "heartbeat_error_type": type(error).__name__})
        code = 3
    args.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = args.state_dir / (stamp + ".json")
    path.write_text(json.dumps(report, indent=2) + "\n")
    for old in sorted(args.state_dir.glob("*.json"))[:-672]:
        old.unlink()  # Reproducible public observations only; never custody data.
    print(json.dumps({"dns_exit_code": report.get("exit_code", 3),
                      "remote_signal_confirmed": report["remote_signal_confirmed"]}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
