#!/usr/bin/env python3
"""Bounded Healthchecks management; secrets stay in memory/encrypted pass."""

import argparse
import json
import pathlib
import re
import subprocess
import urllib.request

API = "https://healthchecks.io/api/v3/checks/"
NAME = "RBX DNS pilot"
SLUG = "rbx-dns-pilot"
API_ENTRY = "rbx/healthchecks/project-api"
PING_ENTRY = "rbx/healthchecks/dns-pilot"
MAX_RESPONSE = 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("redirect_rejected")


def stored(entry):
    result = subprocess.run(["pass", "show", entry], capture_output=True,
                            check=True, timeout=30)
    return result.stdout.splitlines()[0].decode("ascii").strip()


def api(key, url=API, payload=None):
    if not url.startswith(API):
        raise ValueError("unexpected_api_destination")
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode() if payload is not None else None,
        headers={"X-Api-Key": key, "Content-Type": "application/json"})
    opener = urllib.request.build_opener(NoRedirect)
    with opener.open(request, timeout=15) as response:
        raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise ValueError("oversized_api_response")
        return json.loads(raw)


def reconcile(apply=False, drill=False):
    key = stored(API_ENTRY)
    result = api(key)
    matches = [check for check in result["checks"] if check["name"] == NAME]
    if len(matches) > 1:
        raise ValueError("duplicate_owned_check_names")
    desired = {"name": NAME, "slug": SLUG, "tags": "rbx synthetic dns",
               "timeout": 60 if drill else 900, "grace": 60 if drill else 900,
               "channels": "*"}
    existing = matches[0] if matches else None
    if existing and (existing.get("slug") != SLUG or existing.get("schedule")):
        raise ValueError("existing_check_is_not_owned_simple_monitor")
    changed = existing is None or not existing.get("channels") or any(existing.get(k) != v for k, v in
                                      desired.items() if k != "channels")
    report = {"scope": "one named synthetic DNS check", "name": NAME,
              "desired_period_seconds": desired["timeout"],
              "desired_grace_seconds": desired["grace"],
              "other_checks_preserved": len(result["checks"]) - len(matches),
              "change_needed": changed, "apply_requested": apply,
              "temporary_deadman_drill": drill}
    if not apply:
        return report
    # No general API caller or delete operation exists in this controller.
    target = API if existing is None else existing["update_url"]
    check = api(key, target, dict(desired, unique=["name"])) if changed else existing
    if check["name"] != NAME or not re.fullmatch(
            r"https://hc-ping\.com/[0-9a-f-]{36}", check["ping_url"]):
        raise ValueError("unexpected_check_response")
    if not check.get("channels"):
        raise ValueError("notification_channel_not_attached")
    path = pathlib.Path.home() / ".password-store" / (PING_ENTRY + ".gpg")
    if path.exists():
        if stored(PING_ENTRY) != check["ping_url"]:
            raise ValueError("existing_ping_custody_differs")
    else:
        subprocess.run(["pass", "insert", "--multiline", PING_ENTRY],
                       input=(check["ping_url"] + "\n").encode(),
                       capture_output=True, check=True, timeout=30)
    report["reconciled"] = True
    report["ping_stored_encrypted"] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--temporary-deadman-drill", action="store_true")
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Preserve previous receipts")
    try:
        result = reconcile(args.apply, args.temporary_deadman_drill)
        code = 0
    except Exception as error:
        # HTTP errors include credential-bearing endpoints in their string form.
        result, code = {"operation_completed": False,
                        "error_type": type(error).__name__}, 1
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
