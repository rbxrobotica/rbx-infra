#!/usr/bin/env python3
"""Stage sandbox-only ALTCHA and public tenant values in the Commerce source Secret.

Operator-only. Generating pass entries and running this script require separate,
specific approval. Neither value is passed on the command line or printed.
"""

import base64
import binascii
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path


NAMESPACE = "rbx-ia-br"
SECRET_NAME = "rbx-commerce-sandbox-secrets"
INERT_COMMS_URL = "http://127.0.0.1:1"
PRODUCTION_TENANT_ID = uuid.UUID("885f24d2-1217-534e-bb1b-53440a3c04bb")
PASS_ENTRIES = {
    "ALTCHA_SECRET": "rbx/commerce-sandbox/altcha-secret",
    "COMMERCE_PUBLIC_TENANT_ID": "rbx/commerce-sandbox/public-tenant-id",
}
REQUIRED_EXISTING_KEYS = {
    "DATABASE_URL", "COMMS_API_URL", "ASAAS_API_KEY", "ASAAS_WEBHOOK_TOKEN",
    "COMMS_SERVICE_API_KEY", "SATWAKE_EMAIL_SINK_OPERATOR_KEY",
}


class StagingError(Exception):
    """An expected fail-closed precondition or command failure."""


def run_command(argv: list[str], *, input_bytes: bytes | None = None) -> bytes:
    # Kubernetes can echo patch content on error. Keep diagnostics and patch
    # output (which can contain Secret data) off the operator's terminal.
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
        raise StagingError("a required command failed; no value was printed") from exc
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
        if REQUIRED_EXISTING_KEYS - data.keys():
            raise StagingError("required sandbox source properties are absent")
        if any(name in data for name in PASS_ENTRIES):
            raise StagingError("sandbox ALTCHA or tenant property already exists; refusing overwrite")
        url = base64.b64decode(data["COMMS_API_URL"], validate=True).decode("ascii")
        if url != INERT_COMMS_URL:
            raise StagingError("sandbox Comms URL is not inert; refusing staging")
        return resource_version
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise StagingError("source Secret metadata or inert URL check failed") from exc


def read_pass_entry(entry: str) -> bytes:
    raw = run_command(["pass", "show", entry])
    # One final LF is the normal pass storage format. Reject any other line,
    # whitespace or carriage return so a copied note cannot become a key.
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    return raw


def read_altcha_key() -> bytes:
    raw = read_pass_entry(PASS_ENTRIES["ALTCHA_SECRET"])
    if re.fullmatch(rb"[0-9a-f]{64}", raw) is None:
        raise StagingError("sandbox ALTCHA key must be one 64-character lowercase hex value")
    return raw


def read_tenant_id() -> bytes:
    raw = read_pass_entry(PASS_ENTRIES["COMMERCE_PUBLIC_TENANT_ID"])
    try:
        value = raw.decode("ascii")
        tenant_id = uuid.UUID(value)
    except (UnicodeDecodeError, ValueError) as exc:
        raise StagingError("sandbox tenant must be a canonical UUIDv4") from exc
    if (value != str(tenant_id) or tenant_id.version != 4
            or tenant_id in (uuid.UUID(int=0), PRODUCTION_TENANT_ID)):
        raise StagingError("sandbox tenant must be a unique canonical UUIDv4")
    return raw


def stage(kubeconfig: str) -> None:
    resource_version = read_source_secret(kubeconfig)
    values = {
        "ALTCHA_SECRET": read_altcha_key(),
        "COMMERCE_PUBLIC_TENANT_ID": read_tenant_id(),
    }

    # The test and both adds are one atomic JSON Patch. Any intervening
    # Secret update aborts instead of replacing another actor's properties.
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
        print("usage: stage-satwake-sandbox-core-secrets.py /path/to/reviewed-kubeconfig", file=sys.stderr)
        return 2
    try:
        stage(sys.argv[1])
    except StagingError as exc:
        print(f"staging stopped: {exc}", file=sys.stderr)
        return 1
    print("sandbox ALTCHA and tenant properties staged; verify property names only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
