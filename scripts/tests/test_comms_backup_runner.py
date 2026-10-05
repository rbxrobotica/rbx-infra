"""Failure-oriented tests for the Comms-only preservation boundary."""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from comms import backup_runner as runner
from plausible import s3_archive as archive

FP = "A" * 40


class FakeCommands:
    def __init__(self, *, failure=None, wrong_key=False, private=False, receipt_changes=None):
        self.failure = failure
        self.wrong_key = wrong_key
        self.private = private
        self.receipt_changes = receipt_changes or {}
        self.calls = []
        self.manifest = None
        self.ciphertext = None

    def __call__(self, argv, *, env, timeout, label, output=None, max_bytes=None):
        self.calls.append((argv, env, label, timeout))
        if label == self.failure:
            raise runner.BackupError(label + " failed")
        if label == "Public key inspection":
            return ("pub:u:4096:1:KEY:0:0:::::e:\nfpr:::::::::" + ("B" * 40 if self.wrong_key else FP) + ":\n").encode()
        if label == "Private key exclusion":
            return b"sec:u:4096" if self.private else b""
        if label == "PostgreSQL client version":
            return b"pg_dump (PostgreSQL) 16.15\n"
        if label == "PostgreSQL snapshot":
            output.write(b"PGDMP" + b"synthetic-rows" * 20)
        if label == "PostgreSQL archive inspection":
            return b"; Dumped from database version: 16.15\n"
        if label == "Backup encryption":
            with tarfile.open(argv[-1]) as bundle:
                assert set(bundle.getnames()) == {"postgres.dump", "manifest.json"}
                self.manifest = json.load(bundle.extractfile("manifest.json"))
                dump = bundle.extractfile("postgres.dump").read()
                assert self.manifest["files"]["postgres.dump"]["sha256"] == hashlib.sha256(dump).hexdigest()
            output.write(b"synthetic-encrypted-data")
        if label == "Off-site archive verification":
            path = Path(argv[-1])
            assert path.suffix == ".gpg"
            assert path.stat().st_mode & 0o777 == 0o600
            assert path.parent.stat().st_mode & 0o777 == 0o700
            self.ciphertext = path.read_bytes()
            result = {"schema": "rbx.comms.archive-receipt.v1", "bucket": "rbx-data-lake",
                      "key": "comms/backups/full/2026-10-05/unique.gpg", "version_id": "version-1",
                      "sha256": hashlib.sha256(self.ciphertext).hexdigest(), "size_bytes": len(self.ciphertext),
                      "verified_at": "2026-10-05T00:00:00Z"}
            return json.dumps({**result, **self.receipt_changes}).encode()
        return b""


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.key = self.path / "recipient.asc"
        self.key.write_text("synthetic-public-key")
        self.env = {"DATABASE_URL": "postgres://rbx_comms:synthetic-password@database/rbx_comms",
                    "BACKUP_PUBLIC_KEY_FILE": str(self.key), "BACKUP_RECIPIENT_FINGERPRINT": FP,
                    "BACKUP_STAGING_DIR": self.temp.name, "AWS_ACCESS_KEY_ID": "synthetic-access",
                    "AWS_SECRET_ACCESS_KEY": "synthetic-storage-key", "PGOPTIONS": "unsafe",
                    "POSTMARK_SERVER_TOKEN": "synthetic-unrelated-token", "GNUPGHOME": "/untrusted"}

    def invoke(self, commands):
        with patch.object(runner, "run_command", commands):
            return runner.create_backup(runner.Config(self.env))

    def assert_clean(self):
        self.assertEqual(list(self.path.iterdir()), [self.key])

    def test_complete_archive_keeps_secrets_out_of_manifest_and_unrelated_children(self):
        fake = FakeCommands()
        receipt = self.invoke(fake)
        self.assertEqual(receipt["schema"], "rbx.comms.backup-receipt.v1")
        self.assertIsNone(receipt["restore_tested_at"])
        self.assertIsNone(fake.manifest["restore_tested_at"])
        self.assertEqual(fake.manifest["sources"]["postgres"]["database"], "rbx_comms")
        for argv, env, label, timeout in fake.calls:
            self.assertGreater(timeout, 0)
            self.assertNotIn("DATABASE_URL", env)
            self.assertNotIn("GNUPGHOME", env)
            self.assertNotIn("POSTMARK_SERVER_TOKEN", env)
            self.assertNotIn("synthetic-password", " ".join(argv))
            self.assertNotIn("kubectl", argv)
            if label == "PostgreSQL snapshot":
                self.assertEqual(env["PGOPTIONS"], "-c default_transaction_read_only=on")
                self.assertEqual(env["PGDATABASE"], "rbx_comms")
                self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
            else:
                self.assertNotIn("PGPASSWORD", env)
            self.assertEqual("AWS_SECRET_ACCESS_KEY" in env, label == "Off-site archive verification")
        self.assertNotIn("synthetic-password", json.dumps(fake.manifest))
        self.assert_clean()

    def test_early_and_late_failures_clean_plaintext_and_never_report_success(self):
        for label in ("Public key inspection", "PostgreSQL snapshot", "PostgreSQL archive inspection",
                      "Backup encryption", "Off-site archive verification"):
            with self.subTest(label=label):
                fake = FakeCommands(failure=label)
                with self.assertRaises(runner.BackupError): self.invoke(fake)
                if label != "Off-site archive verification":
                    self.assertNotIn("Off-site archive verification", [c[2] for c in fake.calls])
                self.assert_clean()

    def test_wrong_or_private_key_stops_before_database_access(self):
        for kwargs in ({"wrong_key": True}, {"private": True}):
            fake = FakeCommands(**kwargs)
            with self.assertRaises(runner.BackupError): self.invoke(fake)
            self.assertNotIn("PostgreSQL snapshot", [c[2] for c in fake.calls])
            self.assert_clean()

    def test_receipt_cannot_redirect_namespace_or_claim_wrong_hash_or_version(self):
        for change in ({"key": "plausible/backups/full/a.gpg"}, {"key": "comms/backups/restore-proof/a.gpg"},
                       {"key": "comms/backups/full/../a.gpg"}, {"version_id": "null"}, {"version_id": None},
                       {"sha256": "0" * 64}, {"size_bytes": 1}, {"schema": "rbx.plausible.archive-receipt.v1"}):
            with self.subTest(change=change):
                with self.assertRaises(runner.BackupError): self.invoke(FakeCommands(receipt_changes=change))
                self.assert_clean()

    def test_database_scope_url_options_and_caps_fail_before_commands(self):
        cases = [("DATABASE_URL", "postgres://a:b@db/postgres"),
                 ("DATABASE_URL", "postgres://a:b@db/rbx_comms?options=-cfoo"),
                 ("BACKUP_MAX_BYTES", str(1024 ** 3 + 1)), ("BACKUP_MAX_BYTES", "0"),
                 ("BACKUP_TIMEOUT_SECONDS", "1801"), ("BACKUP_TIMEOUT_SECONDS", "0")]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                with self.assertRaises(runner.BackupError): runner.Config({**self.env, key: value})

    def test_one_total_deadline_not_one_timeout_per_step(self):
        fake = FakeCommands()
        with patch.object(runner.time, "monotonic", side_effect=[0, 1801]), patch.object(runner, "run_command", fake):
            with self.assertRaisesRegex(runner.BackupError, "total time"):
                runner.create_backup(runner.Config(self.env))
        self.assertFalse(fake.calls)
        self.assert_clean()

    def test_bundle_limit_prevents_encrypting_or_uploading_oversized_plaintext(self):
        self.env["BACKUP_MAX_BYTES"] = "1024"
        fake = FakeCommands()
        with self.assertRaisesRegex(runner.BackupError, "size limit"): self.invoke(fake)
        self.assertNotIn("Backup encryption", [c[2] for c in fake.calls])
        self.assert_clean()


class ArchiveScopeTests(unittest.TestCase):
    def test_comms_upload_receipt_requires_exact_version_readback_in_comms_scope(self):
        class Response(io.BytesIO):
            def __init__(self, data=b""):
                super().__init__(data)
                self.status = 200
                self.headers = {"x-amz-version-id": "comms-version", "Content-Length": "7"}

        cfg = archive.Config("https://storage.example.test", "private-bucket", "eu-central-1", "test", "test", scope="comms")
        client = archive.ArchiveClient(cfg)
        with tempfile.TemporaryDirectory() as directory:
            encrypted = Path(directory) / "test.gpg"
            encrypted.write_bytes(b"fixture")
            with patch.object(client, "check_bucket"), patch.object(client, "_request", side_effect=[
                    Response(), Response(), Response(b"fixture")]) as request:
                receipt = client.upload(encrypted)
        self.assertEqual(receipt["schema"], "rbx.comms.archive-receipt.v1")
        self.assertTrue(receipt["key"].startswith("comms/backups/full/"))
        self.assertEqual(receipt["version_id"], "comms-version")
        self.assertEqual(receipt["sha256"], hashlib.sha256(b"fixture").hexdigest())
        self.assertEqual([call.args[0] for call in request.call_args_list], ["PUT", "HEAD", "GET"])
        self.assertTrue(all(call.kwargs["key"].startswith("comms/backups/full/") for call in request.call_args_list))
        self.assertEqual(request.call_args_list[-1].kwargs["query"], {"versionId": "comms-version"})

    def test_namespaces_remain_disjoint_and_default_is_plausible(self):
        self.assertTrue(archive.new_key(".gpg").startswith("plausible/backups/full/"))
        self.assertTrue(archive.new_key(".gpg", scope="comms").startswith("comms/backups/full/"))
        for scope, other in (("plausible", "comms"), ("comms", "plausible")):
            with self.assertRaises(archive.ArchiveError): archive.validate_key(other + "/backups/full/x.gpg", scope=scope)
        for scope in ("../comms", "", "arbitrary"):
            with self.assertRaises(archive.ArchiveError): archive.scope_prefix(scope)

    def test_comms_signing_and_listing_cannot_use_plausible_keys(self):
        cfg = archive.Config("https://storage.example.test", "private-bucket", "eu-central-1", "test", "test", scope="comms")
        client = archive.ArchiveClient(cfg)
        with self.assertRaises(archive.ArchiveError):
            client.head("plausible/backups/full/a.gpg")
        request = client._signed_request("HEAD", "comms/backups/full/a.gpg", {}, {}, archive.EMPTY_SHA256, None)
        self.assertIn("/private-bucket/comms/backups/full/a.gpg", request.full_url)


if __name__ == "__main__":
    unittest.main()
