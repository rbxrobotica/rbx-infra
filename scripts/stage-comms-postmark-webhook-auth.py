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
import tempfile
import time


PASS_ENTRY = "rbx/comms/postmark-webhook-auth"
NAMESPACE = "rbx-ia-br"
SECRET_NAME = "comms-postmark-webhook-auth"
RECIPIENT_FINGERPRINT = "88C7C5F7E0FB38128BAA46BC5807B2AAF43E3E41"


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


def pass_store() -> Path:
    return Path(os.environ.get("PASSWORD_STORE_DIR", "~/.password-store")).expanduser()


def pass_path() -> Path:
    return pass_store() / (PASS_ENTRY + ".gpg")


def validate_recipient(target: Path) -> None:
    """Resolve the nearest pass policy and require exactly the reviewed key."""
    root = pass_store().resolve(strict=True)
    parent = target.parent.resolve()
    if not parent.is_relative_to(root):
        raise ProvisioningError("credential path is outside the password store")
    while True:
        policy = parent / ".gpg-id"
        if policy.is_file():
            break
        if parent == root:
            raise ProvisioningError("password store recipient policy is absent")
        parent = parent.parent
    try:
        with policy.open("rb") as stream:
            raw_policy = stream.read(4097)
        recipients = [line.strip() for line in raw_policy.decode("utf-8").splitlines() if line.strip()]
        if len(raw_policy) > 4096 or len(recipients) != 1:
            raise ValueError
    except (ValueError, UnicodeError):
        raise ProvisioningError("recipient policy must name one reviewed key") from None
    listing = run(["gpg", "--no-options", "--batch", "--no-tty", "--with-colons",
                   "--with-fingerprint", "--list-keys", "--", recipients[0]])
    try:
        records = [line.split(":") for line in listing.decode("utf-8").splitlines()]
        public = [record for record in records if record[0] == "pub"]
        if len(public) != 1:
            raise ValueError
        key = public[0]
        primary_index = records.index(key)
        fingerprint = records[primary_index + 1]
        if (key[1] in {"r", "e", "d", "i"} or "D" in key[11] or "E" not in key[11]
                or (key[6] and int(key[6]) <= time.time())
                or fingerprint[0] != "fpr" or fingerprint[9] != RECIPIENT_FINGERPRINT):
            raise ValueError
    except (ValueError, UnicodeError, IndexError):
        raise ProvisioningError("recipient policy does not resolve to the valid pinned encryption key") from None


def publish_ciphertext(target: Path, ciphertext: bytes) -> None:
    """Only encrypted bytes reach disk; hard-link publication cannot overwrite."""
    if not 64 <= len(ciphertext) <= 16384 or ciphertext[0] < 128:
        raise ProvisioningError("GPG returned an unexpected encrypted payload")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".postmark-encrypted-", dir=target.parent,
                                         delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            stream.write(ciphertext)
            stream.flush()
            os.fsync(stream.fileno())
        # Unlike rename/replace, link fails if any target entry already exists,
        # including a symlink or a concurrently created credential.
        os.link(temporary, target)
        directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except FileExistsError:
        raise ProvisioningError("dedicated pass entry already exists; refusing overwrite") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def generate() -> None:
    target = pass_path()
    if os.path.lexists(target):
        raise ProvisioningError("dedicated pass entry already exists; refusing overwrite")
    validate_recipient(target)
    # 48 random bytes produce 64 URL-safe characters, below bcrypt's 72-byte
    # password boundary and safe for separately constructed provider URLs.
    value = {"username": "rbx-postmark-" + secrets.token_hex(8),
             "password": secrets.token_urlsafe(48)}
    # Direct encryption avoids pass insert's automatic Git commit, which could
    # include unrelated staged work. Trust is overridden only for this process
    # after resolving the actual pass policy to the explicitly pinned key.
    ciphertext = run([
        "gpg", "--no-options", "--batch", "--no-tty", "--trust-model", "always",
        "--no-encrypt-to", "--cipher-algo", "AES256", "--output", "-",
        "--encrypt", "--recipient", RECIPIENT_FINGERPRINT,
    ], data=json.dumps(value, separators=(",", ":")).encode("ascii") + b"\n")
    publish_ciphertext(target, ciphertext)


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
