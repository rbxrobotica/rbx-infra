#!/usr/bin/env python3
"""Explicit, bounded issuance/renewal runner; never installs or reloads Ergo.

Default is a credential-free plan. Execution requires a pinned local lego binary,
private TSIG credential file, private existing state directory and --execute.
Production DNS prerequisites must be deployed separately with operator approval.
"""
import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

DOMAIN = "irc.rbxsystems.ch"
SERVERS = {
    "staging": "https://acme-staging-v02.api.letsencrypt.org/directory",
    "production": "https://acme-v02.api.letsencrypt.org/directory",
}


def command(binary, state, email, environment, operation):
    if environment not in SERVERS or operation not in ("issue", "renew"):
        raise ValueError("invalid_mode")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ValueError("invalid_email")
    result = [str(binary), "--path", str(state), "--server", SERVERS[environment],
              "--email", email, "--accept-tos", "--dns", "rfc2136",
              "--domains", DOMAIN]
    return result + (["run"] if operation == "issue" else ["renew", "--days", "30"])


def private_path(path, directory=False):
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("absolute_non_symlink_path_required")
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("private_owned_path_required")
    # Ancestors must not permit unrelated users to replace a verified path.
    for parent in path.parents:
        if parent.stat().st_mode & 0o022:
            raise ValueError("writable_ancestor")


def run(args):
    state = args.state / args.environment
    invocation = command(args.lego, state, args.email, args.environment, args.operation)
    if not args.execute:
        return {"status": "plan_only", "domain": DOMAIN,
                "environment": args.environment, "operation": args.operation,
                "deploys_certificate": False}
    if not re.fullmatch(r"[0-9a-f]{64}", args.sha256 or ""):
        raise ValueError("verified_binary_sha256_required")
    if not args.lego.is_absolute() or args.lego.resolve() != args.lego:
        raise ValueError("absolute_non_symlink_binary_required")
    info = args.lego.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022
            or info.st_uid not in (0, os.geteuid())):
        raise ValueError("unsafe_binary")
    for parent in args.lego.parents:
        if parent.stat().st_mode & 0o022:
            raise ValueError("writable_binary_ancestor")
    if hashlib.sha256(args.lego.read_bytes()).hexdigest() != args.sha256:
        raise ValueError("binary_hash_mismatch")
    private_path(state, directory=True)
    private_path(args.tsig_secret)
    payload = args.tsig_secret.read_bytes().strip()
    if len(payload) > 4096 or len(base64.b64decode(payload, validate=True)) < 32:
        raise ValueError("invalid_or_empty_credential")
    cert = state / "certificates" / (DOMAIN + ".crt")
    if args.operation == "renew" and not cert.is_file():
        raise ValueError("initial_issuance_required")
    if args.operation == "issue" and cert.exists():
        raise ValueError("certificate_already_exists_use_renew")
    env = {
        "PATH": "/usr/bin:/bin", "HOME": str(state),
        "DNSUPDATE_NAMESERVER": "149.102.139.33:53",
        "DNSUPDATE_TSIG_KEY": "rbx-irc-acme.",
        "DNSUPDATE_TSIG_ALGORITHM": "hmac-sha256.",
        "DNSUPDATE_TSIG_SECRET_FILE": str(args.tsig_secret),
        "DNSUPDATE_PROPAGATION_TIMEOUT": "180",
        "DNSUPDATE_TTL": "120",
        "DNSUPDATE_ZONES": "_acme-challenge.irc.rbxsystems.ch.",
    }
    previous = os.umask(0o077)
    try:
        fd = os.open(state / "renew.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = subprocess.run(invocation, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=900, check=False)
            if result.returncode:
                raise ValueError("acme_failed_no_deployment_performed")
    finally:
        os.umask(previous)
    return {"status": "acme_command_succeeded", "domain": DOMAIN,
            "environment": args.environment, "deploys_certificate": False,
            "next": "Verify artifact freshness, trust, hostname and key pairing before deployment"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lego", type=Path, required=True)
    parser.add_argument("--sha256")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--tsig-secret", type=Path)
    parser.add_argument("--email", required=True)
    parser.add_argument("--environment", choices=SERVERS, default="staging")
    parser.add_argument("--operation", choices=("issue", "renew"), default="renew")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        if args.execute and args.tsig_secret is None:
            raise ValueError("credential_file_required")
        print(json.dumps(run(args)))
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        # Never print provider output or exception paths/credential material.
        print(json.dumps({"status": "failed", "category": type(error).__name__,
                          "deploys_certificate": False}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
