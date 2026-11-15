"""Public Presence admission and cross-application preservation regressions."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from comms import backup_runner as runner
from plausible import s3_archive as archive
from test_comms_backup_runner import FakeCommands


class PresenceCommands(FakeCommands):
    def __call__(self, argv, **kwargs):
        if kwargs["label"] == "Off-site archive verification":
            self.calls.append((argv, kwargs["env"], kwargs["label"], kwargs["timeout"]))
            data = Path(argv[-1]).read_bytes()
            return json.dumps({"schema": "rbx.public-presence.archive-receipt.v1",
                "bucket": "synthetic-external-backup", "key": "public-presence/backups/full/2026-11-14/a.gpg",
                "version_id": "synthetic-version", "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data), "verified_at": "2026-11-14T00:00:00Z"}).encode()
        return super().__call__(argv, **kwargs)


class PresenceBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.key = self.base / "recipient.asc"
        self.key.write_text("synthetic-public-key")
        self.env = {"DATABASE_URL": "postgres://fixture:synthetic@db/public_presence",
            "BACKUP_PUBLIC_KEY_FILE": str(self.key), "BACKUP_RECIPIENT_FINGERPRINT": "A" * 40,
            "BACKUP_STAGING_DIR": str(self.base), "UNRELATED_SECRET": "synthetic-unrelated",
            "S3_ARCHIVE_ENDPOINT": "https://storage.example.invalid", "S3_ARCHIVE_BUCKET": "synthetic-backup",
            "AWS_ACCESS_KEY_ID": "synthetic-access", "AWS_SECRET_ACCESS_KEY": "synthetic-key"}

    def test_presence_archive_binds_database_prefix_schema_and_entry_point(self):
        fake = PresenceCommands()
        config = runner.Config(self.env, scope="public-presence")
        with patch.object(runner, "run_command", fake):
            receipt = runner.create_backup(config)
        self.assertEqual(receipt["schema"], "rbx.public-presence.backup-receipt.v1")
        self.assertIsNone(receipt["restore_tested_at"])
        self.assertEqual(fake.manifest["sources"]["postgres"]["database"], "public_presence")
        self.assertEqual(fake.manifest["schema"], "rbx.public-presence.backup-manifest.v1")
        for argv, env, label, _ in fake.calls:
            self.assertNotIn("UNRELATED_SECRET", env)
            if label == "PostgreSQL snapshot":
                self.assertEqual(env["PGDATABASE"], "public_presence")
                self.assertEqual(env["PGOPTIONS"], "-c default_transaction_read_only=on")
            if label == "Off-site archive verification":
                self.assertEqual(Path(argv[1]).parts[-2:], ("presence", "s3_archive.py"))
        self.assertEqual(list(self.base.iterdir()), [self.key])

    def test_database_scope_cannot_be_changed_through_environment(self):
        with self.assertRaises(runner.BackupError): runner.Config(self.env)
        for database in ("postgres", "rbx_comms", "public_presence_other"):
            env = {**self.env, "DATABASE_URL": "postgres://a:b@db/" + database}
            with self.assertRaises(runner.BackupError): runner.Config(env, scope="public-presence")
        with self.assertRaises(runner.BackupError): runner.Config(self.env, scope="other")

    def test_other_application_receipt_cannot_settle_presence_backup(self):
        config = runner.Config(self.env, scope="public-presence")
        with patch.object(runner, "run_command", FakeCommands()):
            with self.assertRaises(runner.BackupError): runner.create_backup(config)
        self.assertEqual(list(self.base.iterdir()), [self.key])

    def test_archive_cli_scopes_remain_separate(self):
        key = "public-presence/backups/full/2026-11-14/a.gpg"
        self.assertEqual(archive.validate_key(key, scope="public-presence"), key)
        for scope in ("comms", "plausible"):
            with self.assertRaises(archive.ArchiveError): archive.validate_key(key, scope=scope)
            with self.assertRaises(archive.ArchiveError):
                archive.validate_key(scope + "/backups/full/2026-11-14/a.gpg", scope="public-presence")

    def test_presence_cannot_silently_inherit_legacy_backup_destination(self):
        for key in ("S3_ARCHIVE_ENDPOINT", "S3_ARCHIVE_BUCKET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
            env = dict(self.env)
            del env[key]
            with self.assertRaises(runner.BackupError): runner.Config(env, scope="public-presence")


if __name__ == "__main__":
    unittest.main()
