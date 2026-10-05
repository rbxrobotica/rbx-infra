#!/usr/bin/env python3
"""Create an encrypted, read-back-verified Plausible database archive.

Required environment: DATABASE_URL, BACKUP_PUBLIC_KEY_FILE and
BACKUP_RECIPIENT_FINGERPRINT, plus the AWS/S3 environment of s3_archive.py.
Only a public encryption key belongs in the Job. The returned receipt proves
ciphertext integrity in versioned storage; it does not prove a restore.
ClickHouse and PostgreSQL have independent consistent snapshots, not a shared
transaction. No data is deleted from either database.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import uuid


MAX_METADATA_BYTES = 64 * 1024
DEFAULT_MAX_BYTES = 1024 * 1024 * 1024
TRANSFER_CHUNK_BYTES = 512 * 1024


class BackupError(Exception):
    """Safe error text: never include subprocess output or source credentials."""


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def bounded_integer(value, low, high, label):
    try:
        number = int(value)
    except (ValueError, TypeError):
        raise BackupError(f"Invalid {label}") from None
    if not low <= number <= high:
        raise BackupError(f"Invalid {label}")
    return number


def source_pg_env(source):
    """Parse credentials into libpq environment only, never command arguments."""
    if (not isinstance(source, str) or not source or len(source) > 8192
            or any(ord(c) < 32 for c in source) or re.search(r"%(?![0-9A-Fa-f]{2})", source)):
        raise BackupError("Invalid PostgreSQL source URL")
    try:
        parsed = urllib.parse.urlsplit(source)
        port = 5432 if parsed.port is None else parsed.port
        username = urllib.parse.unquote(parsed.username or "", errors="strict")
        password = urllib.parse.unquote(parsed.password or "", errors="strict")
        database = urllib.parse.unquote(parsed.path[1:], errors="strict")
        hostname = parsed.hostname or ""
        query = urllib.parse.parse_qs(parsed.query, strict_parsing=True, keep_blank_values=True)
    except (ValueError, UnicodeError):
        raise BackupError("Invalid PostgreSQL source URL") from None
    if (parsed.scheme not in ("postgres", "postgresql") or parsed.fragment
            or not re.fullmatch(r"[A-Za-z0-9.:_-]+", hostname)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,63}", database)
            or not username or not password or not 1 <= port <= 65535
            or any(ord(c) < 32 or ord(c) == 127 for c in username + password)
            or any(key not in ("sslmode", "connect_timeout") or len(values) != 1
                   for key, values in query.items())):
        raise BackupError("Invalid PostgreSQL source URL")
    sslmode = query.get("sslmode", ["prefer"])[0]
    if sslmode not in ("disable", "allow", "prefer", "require", "verify-ca", "verify-full"):
        raise BackupError("Invalid PostgreSQL TLS mode")
    timeout = bounded_integer(query.get("connect_timeout", ["15"])[0], 1, 30,
                              "PostgreSQL connection timeout")
    return {"PGHOST": hostname, "PGPORT": str(port), "PGDATABASE": database,
            "PGUSER": username, "PGPASSWORD": password, "PGSSLMODE": sslmode,
            "PGCONNECT_TIMEOUT": str(timeout), "PGAPPNAME": "plausible-preservation",
            "PGOPTIONS": "-c default_transaction_read_only=on"}


class Config:
    def __init__(self, env=None):
        self.env = dict(os.environ if env is None else env)
        self.pg_env = source_pg_env(self.env.get("DATABASE_URL"))
        self.namespace = self.env.get("CLICKHOUSE_NAMESPACE", "plausible")
        self.pod = self.env.get("CLICKHOUSE_POD", "plausible-clickhouse-0")
        self.database = self.env.get("CLICKHOUSE_DATABASE", "plausible_events")
        if (not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", self.namespace)
                or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", self.pod)
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", self.database)):
            raise BackupError("Invalid ClickHouse source identifier")
        self.fingerprint = self.env.get("BACKUP_RECIPIENT_FINGERPRINT", "").upper()
        if not re.fullmatch(r"(?:[0-9A-F]{40}|[0-9A-F]{64})", self.fingerprint):
            raise BackupError("An exact encryption recipient fingerprint is required")
        key_file = self.env.get("BACKUP_PUBLIC_KEY_FILE", "")
        if not key_file:
            raise BackupError("A mounted public encryption key is required")
        self.public_key = Path(key_file)
        # Kubernetes projected Secret files use symlinks. They may be read, but
        # all files written by this runner are created in its private directory.
        if not self.public_key.is_file() or not 1 <= self.public_key.stat().st_size <= MAX_METADATA_BYTES:
            raise BackupError("Invalid public encryption key file")
        self.max_bytes = bounded_integer(self.env.get("BACKUP_MAX_BYTES", DEFAULT_MAX_BYTES),
                                         1024, 16 * 1024 ** 3, "backup size limit")
        self.timeout = bounded_integer(self.env.get("BACKUP_COMMAND_TIMEOUT_SECONDS", 1800),
                                       1, 3600, "command timeout")
        self.staging = Path(self.env.get("BACKUP_STAGING_DIR", tempfile.gettempdir()))
        self.archive_script = Path(__file__).with_name("s3_archive.py")

    def child_env(self, *, postgres=False, archive=False):
        result = {key: value for key, value in self.env.items()
                  if key != "DATABASE_URL" and not key.startswith("PG")
                  and not key.startswith("AWS_")}
        if postgres:
            result.update(self.pg_env)
        if archive:
            result.update({key: value for key, value in self.env.items() if key.startswith("AWS_")})
        return result


def run_command(argv, *, env, timeout, label, output=None, max_bytes=MAX_METADATA_BYTES):
    """Bound both execution time and streamed output without a shell.

    Stderr is intentionally discarded: PostgreSQL and Kubernetes errors may
    contain connection strings or other credentials. Only safe step labels are
    surfaced. The caller's file remains private and is removed on every exit.
    """
    process = None
    chunks, total = [], 0
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, env=env, close_fds=True)
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BackupError(f"{label} exceeded its time limit")
                if not selector.select(remaining):
                    raise BackupError(f"{label} exceeded its time limit")
                chunk = os.read(process.stdout.fileno(), 64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise BackupError(f"{label} exceeded its size limit")
                if output is None:
                    chunks.append(chunk)
                else:
                    output.write(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0 or process.wait(timeout=remaining) != 0:
            raise BackupError(f"{label} failed")
        return b"".join(chunks)
    except (OSError, subprocess.SubprocessError):
        raise BackupError(f"{label} failed") from None
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.wait()
            if process.stdout is not None:
                process.stdout.close()


def one_json(raw, label):
    try:
        parsed = json.loads(raw)
    except (ValueError, UnicodeError):
        raise BackupError(f"Invalid {label} response") from None
    if not isinstance(parsed, dict):
        raise BackupError(f"Invalid {label} response")
    return parsed


def file_metadata(path):
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return {"size_bytes": size, "sha256": digest.hexdigest()}


def remote_file_metadata(invoke, kubectl, path, *, maximum, deadline, phase):
    """Bind the native archive to its byte count and hash before and after copy."""
    raw_size = invoke(kubectl + ["stat", "-c", "%s", "--", path],
                      f"ClickHouse native size {phase}", deadline=deadline)
    try:
        size = int(raw_size.strip())
    except ValueError:
        raise BackupError("Invalid ClickHouse native archive size") from None
    if not 1 <= size <= maximum:
        raise BackupError("ClickHouse native archive exceeds its size bounds")
    raw_hash = invoke(kubectl + ["sha256sum", "--", path],
                      f"ClickHouse native hash {phase}", deadline=deadline)
    try:
        parts = raw_hash.decode("ascii").strip().split()
    except UnicodeError:
        raise BackupError("Invalid ClickHouse native archive hash") from None
    if len(parts) != 2 or parts[1] != path or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
        raise BackupError("Invalid ClickHouse native archive hash")
    return {"size_bytes": size, "sha256": parts[0]}


class LimitedWriter:
    def __init__(self, stream, maximum):
        self.stream, self.maximum, self.size = stream, maximum, 0

    def write(self, data):
        self.size += len(data)
        if self.size > self.maximum:
            raise BackupError("Backup bundle exceeded its size limit")
        return self.stream.write(data)


def validate_key_listing(raw, fingerprint):
    try:
        lines = [line.split(":") for line in raw.decode("utf-8").splitlines() if line]
    except UnicodeError:
        raise BackupError("Invalid public encryption key") from None
    primary, expect_primary, encryption = [], False, False
    for fields in lines:
        kind = fields[0]
        if kind in ("sec", "ssb"):
            raise BackupError("Private keys must not be mounted in the backup Job")
        if kind == "pub":
            expect_primary = True
        if kind in ("pub", "sub"):
            if len(fields) < 12 or fields[1] in ("r", "e", "d"):
                raise BackupError("Encryption key is revoked, expired or unusable")
            encryption = encryption or "e" in fields[11].lower()
        if kind == "fpr" and expect_primary:
            if len(fields) < 10:
                raise BackupError("Invalid public encryption key")
            primary.append(fields[9].upper())
            expect_primary = False
    if primary != [fingerprint] or not encryption:
        raise BackupError("Public encryption key does not match the pinned recipient")


def validate_archive_receipt(raw, metadata):
    receipt = one_json(raw, "archive")
    if (receipt.get("schema") != "rbx.plausible.archive-receipt.v1"
            or receipt.get("sha256") != metadata["sha256"]
            or receipt.get("size_bytes") != metadata["size_bytes"]
            or not isinstance(receipt.get("version_id"), str)
            or not 1 <= len(receipt["version_id"]) <= 1024
            or receipt["version_id"] == "null"
            or any(ord(c) < 33 or ord(c) > 126 for c in receipt["version_id"])):
        raise BackupError("Archive receipt does not prove ciphertext integrity and versioning")
    bucket, key = receipt.get("bucket", ""), receipt.get("key", "")
    if (not isinstance(bucket, str) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket)
            or not isinstance(key, str) or not re.fullmatch(r"plausible/backups/[A-Za-z0-9_./-]+\.gpg", key)
            or any(part in ("", ".", "..") for part in key.split("/"))):
        raise BackupError("Invalid archive receipt location")
    try:
        verified = dt.datetime.fromisoformat(receipt.get("verified_at", "").replace("Z", "+00:00"))
        if verified.tzinfo is None:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise BackupError("Invalid archive verification timestamp") from None
    return {field: receipt[field] for field in
            ("schema", "bucket", "key", "version_id", "sha256", "size_bytes", "verified_at")}


def create_backup(config):
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex
    remote_file = f"backups/plausible_{run_id}.tar"
    remote_absolute = "/var/lib/clickhouse/backups/" + remote_file
    kubectl = ["kubectl", "exec", "-n", config.namespace, config.pod, "--"]
    clickhouse = kubectl + ["clickhouse-client", "--max_threads=2", "--max_memory_usage=536870912"]

    def invoke(argv, label, *, output=None, maximum=MAX_METADATA_BYTES, postgres=False, archive=False,
               deadline=None):
        timeout = config.timeout if deadline is None else min(config.timeout, deadline - time.monotonic())
        if timeout <= 0:
            raise BackupError("ClickHouse archive transfer exceeded its total time limit")
        return run_command(argv, env=config.child_env(postgres=postgres, archive=archive),
                           timeout=timeout, label=label, output=output, max_bytes=maximum)

    # TemporaryDirectory is mode 0700, including on a shared staging volume.
    # Plaintext remains on the staging filesystem only for this execution. Its
    # enclosing volume should use encrypted storage; encryption at rest is not
    # established by this runner, and unlink is not secure erasure.
    with tempfile.TemporaryDirectory(prefix="plausible-preservation-", dir=config.staging) as directory:
        base = Path(directory)
        os.chmod(base, 0o700)
        gnupg = base / "gnupg"
        gnupg.mkdir(mode=0o700)
        gpg = ["gpg", "--no-options", "--batch", "--no-tty", "--homedir", str(gnupg)]
        listing = invoke(gpg + ["--with-colons", "--import-options", "show-only", "--dry-run",
                               "--import", str(config.public_key)], "Public key inspection")
        validate_key_listing(listing, config.fingerprint)
        invoke(gpg + ["--import", str(config.public_key)], "Public key import")
        secrets = invoke(gpg + ["--with-colons", "--list-secret-keys"], "Private key exclusion")
        if any(line.startswith((b"sec:", b"ssb:")) for line in secrets.splitlines()):
            raise BackupError("Private keys must not be mounted in the backup Job")
        client_version = invoke(["pg_dump", "--version"], "PostgreSQL client version")
        if not re.fullmatch(rb"pg_dump \(PostgreSQL\) 16(?:\.[0-9]+)?(?: [^\r\n]+)?\r?\n?", client_version):
            raise BackupError("PostgreSQL 16 pg_dump is required")
        source = one_json(invoke(clickhouse + [f"--param_database={config.database}", "-q",
            "SELECT version() AS version, (SELECT coalesce(sum(bytes_on_disk), 0) FROM system.parts "
            "WHERE database = {database:String}) AS source_bytes FORMAT JSONEachRow"
        ], "ClickHouse source metadata"), "ClickHouse source metadata")
        source_size = bounded_integer(source.get("source_bytes"), 0, config.max_bytes,
                                       "ClickHouse source size")
        version = source.get("version")
        if not isinstance(version, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,4}", version):
            raise BackupError("Invalid ClickHouse version")
        ch_started = utc_now()
        result = one_json(invoke(clickhouse + ["-q",
            f"BACKUP DATABASE {config.database} TO File('{remote_file}') FORMAT JSONEachRow"
        ], "ClickHouse native backup"), "ClickHouse native backup")
        if result.get("status") != "BACKUP_CREATED":
            raise BackupError("ClickHouse did not confirm BACKUP_CREATED")
        ch_finished = utc_now()
        transfer_deadline = time.monotonic() + config.timeout
        native_before = remote_file_metadata(invoke, kubectl, remote_absolute, maximum=config.max_bytes,
                                             deadline=transfer_deadline, phase="before transfer")
        ch_file = base / "clickhouse.tar"
        transfer_chunks = (native_before["size_bytes"] + TRANSFER_CHUNK_BYTES - 1) // TRANSFER_CHUNK_BYTES
        with ch_file.open("xb") as stream:
            os.chmod(ch_file, 0o600)
            # Full-stream exec truncated real archives even with exit status 0
            # and a drain delay. Read bounded ranges instead; never trust exit
            # status alone. No retry, shell interpolation, or unbounded buffer.
            # Exec count is ceil(native bytes / 512 KiB) + four metadata checks;
            # every range shares one total deadline and the declared byte cap.
            for index in range(transfer_chunks):
                expected = min(TRANSFER_CHUNK_BYTES, native_before["size_bytes"] - index * TRANSFER_CHUNK_BYTES)
                before = stream.tell()
                invoke(kubectl + ["dd", f"if={remote_absolute}", f"bs={TRANSFER_CHUNK_BYTES}",
                                  f"skip={index}", "count=1", "iflag=fullblock", "status=none"],
                       "ClickHouse backup transfer", output=stream, maximum=expected, deadline=transfer_deadline)
                if stream.tell() - before != expected:
                    raise BackupError("ClickHouse archive transfer returned an incomplete block")
        ch_meta = file_metadata(ch_file)
        native_after = remote_file_metadata(invoke, kubectl, remote_absolute, maximum=config.max_bytes,
                                            deadline=transfer_deadline, phase="after transfer")
        if native_before != native_after or ch_meta != native_before:
            raise BackupError("ClickHouse archive transfer failed size or SHA-256 verification")
        pg_file = base / "postgres.dump"
        pg_started = utc_now()
        with pg_file.open("xb") as stream:
            os.chmod(pg_file, 0o600)
            invoke(["pg_dump", "--format=custom", "--no-password", "--no-owner", "--no-privileges",
                    "--lock-wait-timeout=30000"], "PostgreSQL snapshot", output=stream,
                   maximum=config.max_bytes, postgres=True)
        pg_finished = utc_now()
        pg_meta = file_metadata(pg_file)
        with pg_file.open("rb") as stream:
            if stream.read(5) != b"PGDMP":
                raise BackupError("PostgreSQL did not produce a custom-format archive")
        pg_catalog = invoke(["pg_restore", "--list", str(pg_file)], "PostgreSQL archive inspection")
        if not re.search(rb"(?m)^;\s+Dumped from database version: 16(?:\.[0-9]+)?[^\r\n]*$", pg_catalog):
            raise BackupError("PostgreSQL source must be version 16")
        manifest = {
            "schema": "rbx.plausible.backup-manifest.v1", "id": run_id, "created_at": utc_now(),
            "consistency": "independent_database_snapshots", "restore_tested_at": None,
            "encryption": {"format": "OpenPGP", "recipient_fingerprint": config.fingerprint},
            "sources": {
                "clickhouse": {"database": config.database, "version": version,
                               "source_bytes_on_disk": source_size, "snapshot_started_at": ch_started,
                               "snapshot_completed_at": ch_finished, "status": "BACKUP_CREATED",
                               "native_archive_integrity": "size_and_sha256_verified_before_and_after_transfer",
                               "transfer_chunk_bytes": TRANSFER_CHUNK_BYTES,
                               "transfer_exec_calls": transfer_chunks + 4},
                "postgres": {"database": config.pg_env["PGDATABASE"], "major_version": 16,
                             "snapshot_started_at": pg_started, "snapshot_completed_at": pg_finished,
                             "format": "pg_dump_custom"}},
            "files": {"clickhouse.tar": ch_meta, "postgres.dump": pg_meta},
        }
        manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
        manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
        bundle = base / "backup.tar"
        with bundle.open("xb") as stream:
            os.chmod(bundle, 0o600)
            with tarfile.open(fileobj=LimitedWriter(stream, config.max_bytes), mode="w|") as archive:
                for source_file in (ch_file, pg_file):
                    archive.add(source_file, arcname=source_file.name, recursive=False)
                member = tarfile.TarInfo("manifest.json")
                member.size, member.mode = len(manifest_bytes), 0o600
                archive.addfile(member, io.BytesIO(manifest_bytes))
        encrypted = base / "backup.tar.gpg"
        with encrypted.open("xb") as stream:
            os.chmod(encrypted, 0o600)
            invoke(gpg + ["--trust-model", "always", "--no-encrypt-to", "--cipher-algo", "AES256",
                          "--compress-algo", "none", "--recipient", config.fingerprint,
                          "--output", "-", "--encrypt", str(bundle)], "Backup encryption",
                   output=stream, maximum=config.max_bytes)
        encrypted_meta = file_metadata(encrypted)
        if encrypted_meta["size_bytes"] == 0:
            raise BackupError("Encrypted archive is empty")
        archived = validate_archive_receipt(invoke(
            [sys.executable, str(config.archive_script), "--max-bytes", str(config.max_bytes),
             "upload", str(encrypted)], "Off-site archive verification", archive=True), encrypted_meta)
        # Delete only this run's native staging file, only after verified off-site
        # persistence. Failed uploads intentionally leave the source copy intact.
        invoke(kubectl + ["rm", "--", remote_absolute], "Verified remote staging cleanup")
        return {"schema": "rbx.plausible.backup-receipt.v1", "id": run_id,
                "location": f"s3://{archived['bucket']}/{archived['key']}",
                "manifest_sha256": manifest_hash, "verified_at": archived["verified_at"],
                "restore_tested_at": None, "encryption_recipient": config.fingerprint,
                "archive": archived, "remote_staging_removed": True,
                "clickhouse_exec_calls": transfer_chunks + 7}


def main():
    try:
        print(json.dumps(create_backup(Config()), sort_keys=True))
        return 0
    except BackupError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
    except (OSError, ValueError):
        print(json.dumps({"error": "Backup failed; inspect mounted configuration and staging capacity"}),
              file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
