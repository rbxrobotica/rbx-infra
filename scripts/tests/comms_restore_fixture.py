#!/usr/bin/env python3
"""Isolated image smoke: real PostgreSQL/GPG restore; S3 transport is a test stub.

Run inside the built backup image, network=none, writable private /tmp, no
production mounts or credentials. Nothing in this fixture proves a production
archive or live S3 integration; the promotion runbook requires that separately.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

sys.path.insert(0, "/app")
from comms import backup_runner as runner

os.umask(0o077)


def command(args, timeout=60):
    result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError("Synthetic restore command failed: " + Path(args[0]).name)
    return result.stdout


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("comms", "public-presence"), default="comms")
    scope = parser.parse_args(argv).scope
    database = "rbx_comms" if scope == "comms" else "public_presence"
    with tempfile.TemporaryDirectory(prefix="comms-restore-fixture-") as directory:
        base = Path(directory)
        data = base / "postgres"
        gpg_home = base / "private-test-key"
        gpg_home.mkdir(mode=0o700)
        gpg = ["gpg", "--batch", "--no-tty", "--homedir", str(gpg_home)]
        command(gpg + ["--passphrase", "", "--quick-generate-key",
                       "Comms synthetic recovery <backup-test@example.invalid>", "rsa2048", "encrypt", "1d"])
        listing = command(gpg + ["--with-colons", "--list-keys"]).decode()
        fingerprint = next(row.split(":")[9] for row in listing.splitlines() if row.startswith("fpr:"))
        public = base / "recipient.asc"
        public.write_bytes(command(gpg + ["--armor", "--export", fingerprint]))
        command(["initdb", "--auth=trust", "--username=fixture", "--pgdata", str(data)])
        command(["pg_ctl", "-D", str(data), "-l", str(base / "postgres.log"), "-w", "start", "-o",
                 "-h 127.0.0.1 -p 15432 -k " + str(base)])
        try:
            pg = ["psql", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-h", "127.0.0.1", "-p", "15432", "-U", "fixture"]
            command(pg + ["-d", "postgres", "-c", "CREATE DATABASE " + database])
            if scope == "comms":
                sql = ("CREATE SCHEMA comms; CREATE TABLE comms.submissions(id bigint PRIMARY KEY, body text NOT NULL);"
                       "INSERT INTO comms.submissions VALUES (1, 'synthetic one'),(2, 'synthetic two');")
                logical = "SELECT count(*),md5(string_agg(id::text || ':' || body, ',' ORDER BY id)) FROM comms.submissions"
            else:
                sql = ("CREATE SCHEMA presence;"
                    "CREATE TABLE presence.content_items(id bigint PRIMARY KEY, title text NOT NULL);"
                    "CREATE TABLE presence.content_versions(id bigint PRIMARY KEY, item_id bigint REFERENCES presence.content_items, body text NOT NULL);"
                    "CREATE TABLE presence.publications(id bigint PRIMARY KEY, version_id bigint REFERENCES presence.content_versions, receipt jsonb NOT NULL);"
                    "INSERT INTO presence.content_items VALUES (1,'Synthetic draft');"
                    "INSERT INTO presence.content_versions VALUES (2,1,'Synthetic immutable text');"
                    "INSERT INTO presence.publications VALUES (3,2,'{\"synthetic_only\":true,\"remote_ref\":\"fixture\"}');")
                logical = ("SELECT count(*),md5(string_agg(i.title || v.body || p.receipt::text, ',' ORDER BY p.id)) "
                           "FROM presence.publications p JOIN presence.content_versions v ON v.id=p.version_id "
                           "JOIN presence.content_items i ON i.id=v.item_id")
            command(pg + ["-d", database, "-c", sql])
            expected = command(pg + ["-d", database, "-c", logical]).strip()
            original = runner.run_command
            preserved = base / "preserved.gpg"

            def transport_stub(argv, **kwargs):
                if kwargs["label"] != "Off-site archive verification":
                    return original(argv, **kwargs)
                source = Path(argv[-1])
                assert source.suffix == ".gpg"
                shutil.copyfile(source, preserved)
                meta = runner.file_metadata(preserved)
                return json.dumps({"schema": f"rbx.{scope}.archive-receipt.v1", "bucket": "synthetic-private-bucket",
                    "key": f"{scope}/backups/full/2026-10-05/synthetic.gpg", "version_id": "synthetic-version",
                    **meta, "verified_at": runner.utc_now()}).encode()

            runner.run_command = transport_stub
            try:
                receipt = runner.create_backup(runner.Config({
                    **os.environ, "DATABASE_URL": "postgres://fixture:synthetic@127.0.0.1:15432/" + database,
                    "BACKUP_PUBLIC_KEY_FILE": str(public), "BACKUP_RECIPIENT_FINGERPRINT": fingerprint,
                    "BACKUP_STAGING_DIR": str(base), "BACKUP_MAX_BYTES": str(16 * 1024 * 1024),
                    "BACKUP_TIMEOUT_SECONDS": "120", "S3_ARCHIVE_ENDPOINT": "https://storage.example.invalid",
                    "S3_ARCHIVE_BUCKET": "synthetic-private-bucket", "AWS_ACCESS_KEY_ID": "synthetic-access",
                    "AWS_SECRET_ACCESS_KEY": "synthetic-key"}, scope=scope))
            finally:
                runner.run_command = original
            assert receipt["restore_tested_at"] is None
            assert not list(base.glob("comms-preservation-*"))
            decrypted = base / "bundle.tar"
            command(gpg + ["--output", str(decrypted), "--decrypt", str(preserved)])
            with tarfile.open(decrypted, "r:") as bundle:
                members = bundle.getmembers()
                assert {m.name for m in members} == {"postgres.dump", "manifest.json"}
                assert len(members) == 2 and all(m.isfile() and 0 < m.size <= 16 * 1024 * 1024 for m in members)
                manifest_bytes = bundle.extractfile("manifest.json").read()
                assert hashlib.sha256(manifest_bytes).hexdigest() == receipt["manifest_sha256"]
                manifest = json.loads(manifest_bytes)
                assert manifest["schema"] == f"rbx.{scope}.backup-manifest.v1"
                assert manifest["sources"]["postgres"]["database"] == database
                dump = base / "restored.dump"
                with dump.open("xb") as output:
                    shutil.copyfileobj(bundle.extractfile("postgres.dump"), output)
                assert runner.file_metadata(dump) == manifest["files"]["postgres.dump"]
            command(pg + ["-d", "postgres", "-c", "CREATE DATABASE fixture_restore"])
            command(["pg_restore", "--exit-on-error", "--no-owner", "--no-privileges", "-h", "127.0.0.1",
                     "-p", "15432", "-U", "fixture", "-d", "fixture_restore", str(dump)])
            restored = command(pg + ["-d", "fixture_restore", "-c", logical]).strip()
            assert restored == expected
            print(json.dumps({"synthetic_only": True, "s3_transport": "stubbed", "postgres_major": 16,
                              "database_scope": scope, "rows_restored": 2 if scope == "comms" else 3,
                              "logical_fingerprint_match": True,
                              "gpg_decryption_manifest_and_dump_hashes": "verified",
                              "runner_plaintext_cleanup": "verified"}))
        finally:
            command(["pg_ctl", "-D", str(data), "-m", "immediate", "-w", "stop"], timeout=20)
    return 0


if __name__ == "__main__":
    sys.exit(main())
