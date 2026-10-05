#!/usr/bin/env python3
"""Preserve only the rbx_comms PostgreSQL database as an encrypted S3 archive.

The Job receives a public encryption key, never its private counterpart. The
receipt proves exact-version ciphertext readback, not successful restoration.
No SQL write, Kubernetes API request or retention deletion is performed.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tarfile
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from plausible.backup_runner import (  # noqa: E402
    BackupError, LimitedWriter, MAX_METADATA_BYTES, bounded_integer,
    file_metadata, one_json, run_command, source_pg_env, utc_now,
    validate_key_listing,
)
from plausible.s3_archive import validate_key, validate_version, ArchiveError  # noqa: E402


class Config:
    def __init__(self, env=None):
        self.env = dict(os.environ if env is None else env)
        self.pg_env = source_pg_env(self.env.get("DATABASE_URL"))
        if self.pg_env["PGDATABASE"] != "rbx_comms":
            raise BackupError("Only the rbx_comms database may be backed up")
        self.pg_env["PGAPPNAME"] = "comms-preservation"
        self.fingerprint = self.env.get("BACKUP_RECIPIENT_FINGERPRINT", "").upper()
        if not re.fullmatch(r"(?:[0-9A-F]{40}|[0-9A-F]{64})", self.fingerprint):
            raise BackupError("An exact encryption recipient fingerprint is required")
        self.public_key = Path(self.env.get("BACKUP_PUBLIC_KEY_FILE", ""))
        if not self.public_key.is_file() or not 1 <= self.public_key.stat().st_size <= MAX_METADATA_BYTES:
            raise BackupError("A bounded mounted public encryption key is required")
        self.max_bytes = bounded_integer(self.env.get("BACKUP_MAX_BYTES", 1024 ** 3),
                                         1024, 1024 ** 3, "backup size limit")
        self.timeout = bounded_integer(self.env.get("BACKUP_TIMEOUT_SECONDS", 1800),
                                       1, 1800, "backup timeout")
        self.staging = Path(self.env.get("BACKUP_STAGING_DIR", tempfile.gettempdir()))
        self.archive_script = Path(__file__).with_name("s3_archive.py")

    def child_env(self, *, postgres=False, archive=False):
        # Pass only runtime settings and the credentials required for that step.
        result = {k: v for k, v in self.env.items() if k in
                  ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR")}
        if postgres:
            result.update(self.pg_env)
        if archive:
            result.update({k: v for k, v in self.env.items() if k.startswith(("AWS_", "S3_ARCHIVE_"))})
        return result


def validate_receipt(raw, metadata):
    receipt = one_json(raw, "archive")
    if (receipt.get("schema") != "rbx.comms.archive-receipt.v1"
            or receipt.get("sha256") != metadata["sha256"]
            or receipt.get("size_bytes") != metadata["size_bytes"]):
        raise BackupError("Archive receipt does not prove ciphertext integrity")
    try:
        version = receipt.get("version_id")
        if version is None:
            raise ArchiveError("Missing version")
        validate_version(version)
        key = validate_key(receipt.get("key", ""), scope="comms")
        if not key.startswith("comms/backups/full/") or not key.endswith(".gpg"):
            raise ArchiveError("Unexpected archive location")
        bucket = receipt.get("bucket", "")
        if not isinstance(bucket, str) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
            raise ArchiveError("Invalid bucket")
        verified = dt.datetime.fromisoformat(receipt.get("verified_at", "").replace("Z", "+00:00"))
        if verified.tzinfo is None:
            raise ValueError
    except (ArchiveError, ValueError, TypeError, AttributeError):
        raise BackupError("Invalid versioned archive receipt") from None
    return {k: receipt[k] for k in
            ("schema", "bucket", "key", "version_id", "sha256", "size_bytes", "verified_at")}


def create_backup(config):
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex
    deadline = time.monotonic() + config.timeout

    def invoke(argv, label, *, output=None, maximum=MAX_METADATA_BYTES, postgres=False, archive=False):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BackupError("Backup exceeded its total time limit")
        return run_command(argv, env=config.child_env(postgres=postgres, archive=archive),
                           timeout=remaining, label=label, output=output, max_bytes=maximum)

    # Unlinking plaintext is not secure erasure. Storage encryption must be
    # provided by the staging filesystem; this runner does not assert it.
    with tempfile.TemporaryDirectory(prefix="comms-preservation-", dir=config.staging) as directory:
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
        version = invoke(["pg_dump", "--version"], "PostgreSQL client version")
        if not re.fullmatch(rb"pg_dump \(PostgreSQL\) 16(?:\.[0-9]+)?(?: [^\r\n]+)?\r?\n?", version):
            raise BackupError("PostgreSQL 16 pg_dump is required")
        dump = base / "postgres.dump"
        started = utc_now()
        with dump.open("xb") as stream:
            os.chmod(dump, 0o600)
            invoke(["pg_dump", "--format=custom", "--no-password", "--no-owner", "--no-privileges",
                    "--lock-wait-timeout=30000"], "PostgreSQL snapshot", output=stream,
                   maximum=config.max_bytes, postgres=True)
        finished = utc_now()
        with dump.open("rb") as stream:
            if stream.read(5) != b"PGDMP":
                raise BackupError("PostgreSQL did not produce a custom-format archive")
        catalog = invoke(["pg_restore", "--list", str(dump)], "PostgreSQL archive inspection")
        if not re.search(rb"(?m)^;\s+Dumped from database version: 16(?:\.[0-9]+)?[^\r\n]*$", catalog):
            raise BackupError("PostgreSQL source must be version 16")
        manifest = {"schema": "rbx.comms.backup-manifest.v1", "id": run_id, "created_at": utc_now(),
                    "consistency": "single_database_snapshot", "restore_tested_at": None,
                    "encryption": {"format": "OpenPGP", "recipient_fingerprint": config.fingerprint},
                    "sources": {"postgres": {"database": "rbx_comms", "major_version": 16,
                                             "snapshot_started_at": started, "snapshot_completed_at": finished,
                                             "format": "pg_dump_custom"}},
                    "files": {"postgres.dump": file_metadata(dump)}}
        manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
        manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
        bundle = base / "backup.tar"
        with bundle.open("xb") as stream:
            os.chmod(bundle, 0o600)
            with tarfile.open(fileobj=LimitedWriter(stream, config.max_bytes), mode="w|") as archive:
                archive.add(dump, arcname=dump.name, recursive=False)
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
        metadata = file_metadata(encrypted)
        if not metadata["size_bytes"]:
            raise BackupError("Encrypted archive is empty")
        receipt = validate_receipt(invoke(
            [sys.executable, str(config.archive_script), "--max-bytes", str(config.max_bytes),
             "upload", str(encrypted)], "Off-site archive verification", archive=True), metadata)
        return {"schema": "rbx.comms.backup-receipt.v1", "id": run_id,
                "location": f"s3://{receipt['bucket']}/{receipt['key']}",
                "manifest_sha256": manifest_hash, "verified_at": receipt["verified_at"],
                "restore_tested_at": None, "encryption_recipient": config.fingerprint,
                "archive": receipt}


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
