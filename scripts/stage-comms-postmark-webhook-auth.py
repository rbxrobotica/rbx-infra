#!/usr/bin/env python3
"""Create a dedicated pass entry or stage its one new source Secret.

Operator-only provisioning. Neither action is performed on import or in check
mode. Values remain in process memory/stdin; command diagnostics are suppressed.
This script never patches existing credentials, contacts, providers or workloads.
"""

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys


PASS_ENTRY = "rbx/comms/postmark-webhook-auth"
NAMESPACE = "rbx-ia-br"
SECRET_NAME = "comms-postmark-webhook-auth"


class ProvisioningError(Exception):
    """Safe, value-free operational error."""


def run(argv: list[str], *, data: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(
            argv, input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            check=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        raise ProvisioningError("required command failed; diagnostics withheld") from None
    return result.stdout


def pass_path() -> Path:
    store = Path(os.environ.get("PASSWORD_STORE_DIR", "~/.password-store")).expanduser()
    return store / (PASS_ENTRY + ".gpg")


def generate() -> None:
    if pass_path().exists():
        raise ProvisioningError("dedicated pass entry already exists; refusing overwrite")
    # 48 random bytes produce 64 URL-safe characters, below bcrypt's 72-byte
    # password boundary and safe for separately constructed provider URLs.
    value = {"username": "rbx-postmark-" + secrets.token_hex(8),
             "password": secrets.token_urlsafe(48)}
    # No --force: pass must also reject a concurrently-created entry.
    run(["pass", "insert", "--multiline", PASS_ENTRY],
        data=json.dumps(value, separators=(",", ":")).encode("ascii") + b"\n")


def read_credential() -> dict[str, str]:
    raw = run(["pass", "show", PASS_ENTRY])
    try:
        if len(raw) > 4096:
            raise ValueError
        value = json.loads(raw)
        if (not isinstance(value, dict) or set(value) != {"username", "password"}
                or not isinstance(value["username"], str)
                or not isinstance(value["password"], str)
                or re.fullmatch(r"rbx-postmark-[0-9a-f]{16}", value["username"]) is None
                or re.fullmatch(r"[A-Za-z0-9_-]{64}", value["password"]) is None):
            raise ValueError
    except (ValueError, TypeError, UnicodeError):
        raise ProvisioningError("dedicated pass entry has an invalid format") from None
    return value


def stage(kubeconfig: str) -> None:
    path = Path(kubeconfig)
    try:
        if not path.is_file() or path.stat().st_mode & 0o077:
            raise ProvisioningError("kubeconfig must be a private file (mode 0600)")
    except OSError:
        raise ProvisioningError("kubeconfig is unavailable") from None
    if shutil.which("htpasswd") is None:
        raise ProvisioningError("htpasswd is required; no credential was staged")
    kubectl = ["kubectl", "--kubeconfig", str(path), "--namespace", NAMESPACE]
    existing = run(kubectl + ["get", "secret", SECRET_NAME,
                             "--ignore-not-found", "-o", "name"])
    if existing.strip():
        raise ProvisioningError("dedicated source Secret already exists; refusing overwrite")
    value = read_credential()
    # Password is supplied over stdin, never the process argument list. -n
    # writes the hash to captured stdout and creates no plaintext/hash file.
    line = run(["htpasswd", "-niBC", "12", value["username"]],
               data=value["password"].encode("ascii") + b"\n").strip()
    expected = re.escape(value["username"].encode("ascii")) + rb":\$2[aby]\$12\$[./A-Za-z0-9]{53}"
    if re.fullmatch(expected, line) is None:
        raise ProvisioningError("htpasswd returned an unexpected bcrypt record")
    source = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
              "metadata": {"name": SECRET_NAME, "namespace": NAMESPACE},
              "stringData": {**value, "htpasswd": line.decode("ascii") + "\n"}}
    # Create-only is atomic: a race or a prior credential cannot be overwritten.
    # No kubectl apply, broad Ansible role or shared contact Secret is involved.
    run(kubectl + ["create", "-f", "-"],
        data=json.dumps(source, separators=(",", ":")).encode("ascii"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("generate", help="create a new dedicated encrypted pass entry")
    staging = actions.add_parser("stage", help="create only the dedicated source Secret")
    staging.add_argument("--kubeconfig", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "generate":
            generate()
        else:
            stage(args.kubeconfig)
    except ProvisioningError as exc:
        # All ProvisioningError messages are fixed strings, never tool output.
        print(f"Postmark credential operation stopped: {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("Postmark credential operation stopped; OS diagnostics withheld.", file=sys.stderr)
        return 1
    print("Dedicated Postmark credential operation completed; no value was printed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
