#!/usr/bin/env python3
"""Stage two distinct sandbox email keys in the Commerce sandbox source Secret.

Operator-only: this script requires separate approval before its first run.
It never generates a key or sends a key through argv, terminal output, or a
plaintext file. Both values must already exist in dedicated pass entries.
"""

import base64
import binascii
import json
import re
import subprocess
import sys
from pathlib import Path


NAMESPACE = "rbx-ia-br"
SECRET_NAME = "rbx-commerce-sandbox-secrets"
INERT_COMMS_URL = "http://127.0.0.1:1"
PASS_ENTRIES = {
    "COMMS_SERVICE_API_KEY": "rbx/commerce-sandbox/satwake-email-sink-service-key",
    "SATWAKE_EMAIL_SINK_OPERATOR_KEY": "rbx/commerce-sandbox/satwake-email-sink-operator-key",
}


class StagingError(Exception):
    """An expected fail-closed precondition or command failure."""


def run_command(argv: list[str], *, input_bytes: bytes | None = None) -> bytes:
    # Suppress command diagnostics: Kubernetes may echo patch content on an
    # error, and pass diagnostics should never become operator-facing output.
    try:
        result = subprocess.run(
            argv,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=60,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise StagingError("a required command failed; no key value was printed") from exc
    return result.stdout


def read_source_secret(kubeconfig: str) -> str:
    raw = run_command(
        ["kubectl", "--kubeconfig", kubeconfig, "--namespace", NAMESPACE,
         "get", "secret", SECRET_NAME, "-o", "json"]
    )
    try:
        secret = json.loads(raw)
        metadata = secret["metadata"]
        data = secret["data"]
        if (metadata["name"] != SECRET_NAME or metadata["namespace"] != NAMESPACE
                or not isinstance(data, dict)):
            raise ValueError("unexpected Secret identity or data")
        resource_version = metadata["resourceVersion"]
        if not isinstance(resource_version, str) or not resource_version:
            raise ValueError("missing resourceVersion")
        if any(key in data for key in PASS_ENTRIES):
            raise StagingError("one or both sandbox email keys already exist; refusing overwrite")
        url = base64.b64decode(data["COMMS_API_URL"], validate=True).decode("ascii")
        if url != INERT_COMMS_URL:
            raise StagingError("sandbox Comms URL is not inert; refusing key staging")
        return resource_version
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise StagingError("source Secret metadata or inert URL check failed") from exc


def read_pass_key(entry: str) -> bytes:
    raw = run_command(["pass", "show", entry])
    # A pass one-line entry ordinarily includes one final LF. Reject headers,
    # multiple lines, whitespace, non-hex text, and values of another length.
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    if re.fullmatch(rb"[0-9a-f]{64}", raw) is None:
        raise StagingError("sandbox pass entry must contain one 64-character lowercase hex key")
    return raw


def stage(kubeconfig: str) -> None:
    resource_version = read_source_secret(kubeconfig)
    values = {name: read_pass_key(entry) for name, entry in PASS_ENTRIES.items()}
    if values["COMMS_SERVICE_API_KEY"] == values["SATWAKE_EMAIL_SINK_OPERATOR_KEY"]:
        raise StagingError("sandbox service and operator keys must be distinct")

    # JSON Patch evaluates the resourceVersion test and both add operations
    # atomically. An intervening Secret update aborts instead of overwriting
    # another actor's data. Neither key may have existed at the read version.
    patch = [{"op": "test", "path": "/metadata/resourceVersion", "value": resource_version}]
    patch.extend(
        {"op": "add", "path": f"/data/{name}",
         "value": base64.b64encode(values[name]).decode("ascii")}
        for name in PASS_ENTRIES
    )
    run_command(
        ["kubectl", "--kubeconfig", kubeconfig, "--namespace", NAMESPACE,
         "patch", "secret", SECRET_NAME, "--type=json", "--patch-file=/dev/stdin"],
        input_bytes=json.dumps(patch, separators=(",", ":")).encode("ascii"),
    )


def main() -> int:
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_file():
        print("usage: stage-satwake-sandbox-email-keys.py /path/to/reviewed-kubeconfig", file=sys.stderr)
        return 2
    try:
        stage(sys.argv[1])
    except StagingError as exc:
        print(f"staging stopped: {exc}", file=sys.stderr)
        return 1
    print("sandbox email keys staged; verify key names and readiness only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
