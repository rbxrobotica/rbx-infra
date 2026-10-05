"""Offline safety, failure semantics and bounded process tests for backups."""

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "plausible" / "backup_runner.py"
SPEC = importlib.util.spec_from_file_location("plausible_backup_runner", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
FINGERPRINT = "A" * 40
PASSWORD = "secret+never-in-output"


def key_listing(fingerprint=FINGERPRINT, kind="pub", capabilities="scESC", validity="-"):
    fields = [kind, validity, "255", "22", "unused", "0", "0", "", "", "", "", capabilities]
    return (":".join(fields) + ":\nfpr:::::::::" + fingerprint + ":\n").encode()


class FakeCommands:
    def __init__(self, fail=None):
        self.fail = fail
        self.calls = []
        self.manifest = None
        self.wrong_hash = False
        self.status = "BACKUP_CREATED"
        self.key = key_listing()
        self.secrets = b""
        self.server_version = b";     Dumped from database version: 16.15\n"
        self.native = b"example native archive"
        self.truncate_block = False
        self.corrupt_block = False
        self.changed_source = False
        self.clock = None

    def __call__(self, argv, *, env, timeout, label, output=None, max_bytes=None):
        self.calls.append((label, argv, env))
        if label == self.fail:
            raise MODULE.BackupError(f"{label} failed")
        if label == "Public key inspection":
            return self.key
        if label == "Private key exclusion":
            return self.secrets
        if label == "PostgreSQL client version":
            return b"pg_dump (PostgreSQL) 16.15\n"
        if label == "ClickHouse source metadata":
            return b'{"version":"24.12.6.70","source_bytes":"100"}'
        if label == "ClickHouse native backup":
            return json.dumps({"status": self.status}).encode()
        if label.startswith("ClickHouse native size "):
            return str(len(self.native)).encode() + b"\n"
        if label.startswith("ClickHouse native hash "):
            digest = ("0" * 64 if self.changed_source and label.endswith("after transfer")
                      else hashlib.sha256(self.native).hexdigest())
            return (digest + "  " + argv[-1] + "\n").encode()
        if label == "ClickHouse backup transfer":
            self._private_output(output)
            block_size = int(next(arg[3:] for arg in argv if arg.startswith("bs=")))
            index = int(next(arg[5:] for arg in argv if arg.startswith("skip=")))
            block = self.native[index * block_size:(index + 1) * block_size]
            if self.truncate_block:
                block = block[:-1]
            if self.corrupt_block:
                block = bytes([block[0] ^ 1]) + block[1:]
            output.write(block)
            if self.clock is not None:
                self.clock[0] += 2
        if label == "PostgreSQL snapshot":
            self._private_output(output)
            output.write(b"PGDMPexample custom archive")
        if label == "PostgreSQL archive inspection":
            return self.server_version
        if label == "Backup encryption":
            self._private_output(output)
            with tarfile.open(argv[-1]) as bundle:
                assert set(bundle.getnames()) == {"clickhouse.tar", "postgres.dump", "manifest.json"}
                self.manifest = json.load(bundle.extractfile("manifest.json"))
                for name in ("clickhouse.tar", "postgres.dump"):
                    data = bundle.extractfile(name).read()
                    assert self.manifest["files"][name]["sha256"] == hashlib.sha256(data).hexdigest()
                    assert self.manifest["files"][name]["size_bytes"] == len(data)
            output.write(b"example encrypted OpenPGP bytes")
        if label == "Off-site archive verification":
            path = Path(argv[-1])
            assert path.suffix == ".gpg"
            assert path.read_bytes() == b"example encrypted OpenPGP bytes"
            return json.dumps({
                "schema": "rbx.plausible.archive-receipt.v1", "bucket": "private-backups",
                "key": "plausible/backups/2026-10-05/test_uuid.gpg", "version_id": "version-1",
                "size_bytes": path.stat().st_size,
                "sha256": "0" * 64 if self.wrong_hash else hashlib.sha256(path.read_bytes()).hexdigest(),
                "verified_at": "2026-10-05T12:00:00Z",
                "unexpected_secret": PASSWORD,
            }).encode()
        return b""

    @staticmethod
    def _private_output(output):
        assert stat.S_IMODE(os.fstat(output.fileno()).st_mode) == 0o600
        assert stat.S_IMODE(Path(output.name).parent.stat().st_mode) == 0o700

    @property
    def labels(self):
        return [call[0] for call in self.calls]


class BackupRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.key_file = self.base / "public.asc"
        self.key_file.write_text("example public key")
        self.env = {
            "DATABASE_URL": "postgres://analytics:secret%2Bnever-in-output@plausible-postgres:5432/plausible",
            "BACKUP_PUBLIC_KEY_FILE": str(self.key_file),
            "BACKUP_RECIPIENT_FINGERPRINT": FINGERPRINT,
            "BACKUP_STAGING_DIR": str(self.base), "BACKUP_MAX_BYTES": "65536",
            "PATH": os.environ.get("PATH", ""), "AWS_ACCESS_KEY_ID": "access-secret",
            "AWS_SECRET_ACCESS_KEY": "aws-secret", "PGPASSWORD": "stale-wrong-secret",
            "PGOPTIONS": "-c default_transaction_read_only=off",
        }

    def run_fake(self, fake):
        with mock.patch.object(MODULE, "run_command", side_effect=fake):
            return MODULE.create_backup(MODULE.Config(self.env))

    def assert_staging_removed(self):
        self.assertEqual(sorted(path.name for path in self.base.iterdir()), ["public.asc"])

    def test_success_verifies_then_cleans_only_own_absolute_remote_file(self):
        fake = FakeCommands()
        receipt = self.run_fake(fake)
        self.assert_staging_removed()
        self.assertIsNone(receipt["restore_tested_at"])
        self.assertTrue(receipt["remote_staging_removed"])
        self.assertEqual(receipt["location"], "s3://private-backups/plausible/backups/2026-10-05/test_uuid.gpg")
        self.assertEqual(fake.labels[-2:], ["Off-site archive verification", "Verified remote staging cleanup"])
        copy = next(call[1] for call in fake.calls if call[0] == "ClickHouse backup transfer")
        source = next(arg[3:] for arg in copy if arg.startswith("if="))
        remove = fake.calls[-1][1]
        self.assertEqual(remove[-3:], ["rm", "--", source])
        self.assertRegex(source, r"^/var/lib/clickhouse/backups/backups/plausible_[0-9]{8}T[0-9]{6}Z_[0-9a-f]{32}\.tar$")
        backup = next(call[1] for call in fake.calls if call[0] == "ClickHouse native backup")
        self.assertIn(source.removeprefix("/var/lib/clickhouse/backups/"), backup[-1])

    def test_credentials_only_reach_appropriate_child_environment(self):
        fake = FakeCommands()
        receipt = self.run_fake(fake)
        for label, argv, env in fake.calls:
            self.assertNotIn("DATABASE_URL", env)
            self.assertNotIn(PASSWORD, " ".join(argv))
            self.assertNotIn("aws-secret", " ".join(argv))
            if label == "PostgreSQL snapshot":
                self.assertEqual(env["PGPASSWORD"], PASSWORD)
                self.assertEqual(env["PGOPTIONS"], "-c default_transaction_read_only=on")
            else:
                self.assertNotIn("PGPASSWORD", env)
                self.assertNotIn("PGOPTIONS", env)
            self.assertEqual("AWS_SECRET_ACCESS_KEY" in env, label == "Off-site archive verification")
        self.assertNotIn(PASSWORD, json.dumps(receipt) + json.dumps(fake.manifest))
        self.assertEqual(fake.manifest["consistency"], "independent_database_snapshots")
        self.assertEqual(fake.manifest["sources"]["postgres"]["major_version"], 16)
        self.assertIsNone(fake.manifest["restore_tested_at"])

    def test_encryption_pins_recipient_and_only_ciphertext_is_uploaded(self):
        fake = FakeCommands()
        self.run_fake(fake)
        argv = next(call[1] for call in fake.calls if call[0] == "Backup encryption")
        self.assertEqual(argv[argv.index("--recipient") + 1], FINGERPRINT)
        self.assertEqual(argv[argv.index("--trust-model") + 1], "always")
        self.assertIn("--no-options", argv)
        self.assertIn("--no-encrypt-to", argv)
        uploads = [call[1] for call in fake.calls if call[0] == "Off-site archive verification"]
        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0][-2], "upload")
        self.assertTrue(uploads[0][-1].endswith(".gpg"))

    def test_failure_at_each_step_cleans_plaintext_and_does_not_claim_success(self):
        for failure in ("Public key inspection", "Public key import", "Private key exclusion",
                        "PostgreSQL client version", "ClickHouse source metadata", "ClickHouse native backup",
                        "ClickHouse native size before transfer", "ClickHouse native hash before transfer",
                        "ClickHouse backup transfer", "PostgreSQL snapshot", "PostgreSQL archive inspection",
                        "ClickHouse native size after transfer", "ClickHouse native hash after transfer",
                        "Backup encryption", "Off-site archive verification", "Verified remote staging cleanup"):
            with self.subTest(failure=failure):
                fake = FakeCommands(fail=failure)
                with self.assertRaises(MODULE.BackupError):
                    self.run_fake(fake)
                self.assert_staging_removed()
                if failure != "Verified remote staging cleanup":
                    self.assertNotIn("Verified remote staging cleanup", fake.labels)
                if failure not in ("Off-site archive verification", "Verified remote staging cleanup"):
                    self.assertNotIn("Off-site archive verification", fake.labels)

    def test_zero_exit_with_a_truncated_block_never_reaches_postgres_or_upload(self):
        fake = FakeCommands()
        fake.truncate_block = True
        with self.assertRaisesRegex(MODULE.BackupError, "incomplete block"):
            self.run_fake(fake)
        self.assertNotIn("PostgreSQL snapshot", fake.labels)
        self.assertNotIn("Off-site archive verification", fake.labels)
        self.assertNotIn("Verified remote staging cleanup", fake.labels)
        self.assert_staging_removed()

    def test_same_size_corruption_and_changed_source_fail_hash_verification(self):
        for flag in ("corrupt_block", "changed_source"):
            with self.subTest(flag=flag):
                fake = FakeCommands()
                setattr(fake, flag, True)
                with self.assertRaisesRegex(MODULE.BackupError, "SHA-256 verification"):
                    self.run_fake(fake)
                self.assertNotIn("PostgreSQL snapshot", fake.labels)
                self.assertNotIn("Off-site archive verification", fake.labels)
                self.assert_staging_removed()

    def test_complete_blocks_and_partial_tail_reassemble_the_exact_native_archive(self):
        self.env["BACKUP_MAX_BYTES"] = str(2 * 1024 * 1024)
        fake = FakeCommands()
        fake.native = b"a" * MODULE.TRANSFER_CHUNK_BYTES + b"final partial block"
        self.run_fake(fake)
        self.assertEqual(fake.manifest["files"]["clickhouse.tar"], {
            "size_bytes": len(fake.native), "sha256": hashlib.sha256(fake.native).hexdigest()})
        self.assert_staging_removed()

    def test_transfer_commands_share_a_deadline_instead_of_resetting_the_budget(self):
        self.env["BACKUP_COMMAND_TIMEOUT_SECONDS"] = "1"
        fake = FakeCommands()
        fake.clock = [0]
        with mock.patch.object(MODULE.time, "monotonic", side_effect=lambda: fake.clock[0]):
            with self.assertRaisesRegex(MODULE.BackupError, "total time limit"):
                self.run_fake(fake)
        self.assertNotIn("PostgreSQL snapshot", fake.labels)
        self.assertNotIn("Off-site archive verification", fake.labels)
        self.assert_staging_removed()

    def test_noncompleted_native_backup_is_not_copied_or_uploaded(self):
        fake = FakeCommands()
        fake.status = "CREATING_BACKUP"
        with self.assertRaisesRegex(MODULE.BackupError, "BACKUP_CREATED"):
            self.run_fake(fake)
        self.assertNotIn("ClickHouse backup transfer", fake.labels)
        self.assertNotIn("Off-site archive verification", fake.labels)
        self.assert_staging_removed()

    def test_ciphertext_receipt_mismatch_preserves_remote_source(self):
        fake = FakeCommands()
        fake.wrong_hash = True
        with self.assertRaisesRegex(MODULE.BackupError, "ciphertext integrity"):
            self.run_fake(fake)
        self.assertNotIn("Verified remote staging cleanup", fake.labels)
        self.assert_staging_removed()

    def test_wrong_or_private_or_expired_key_fails_before_source_backup(self):
        for listing in (key_listing("B" * 40), key_listing(kind="sec"),
                        key_listing(validity="e"), key_listing(capabilities="scSC"),
                        key_listing() + key_listing("B" * 40)):
            with self.subTest(listing=listing):
                fake = FakeCommands()
                fake.key = listing
                with self.assertRaises(MODULE.BackupError):
                    self.run_fake(fake)
                self.assertEqual(fake.labels, ["Public key inspection"])
                self.assert_staging_removed()

    def test_private_key_in_imported_keyring_fails_before_database_backup(self):
        fake = FakeCommands()
        fake.secrets = key_listing(kind="sec")
        with self.assertRaisesRegex(MODULE.BackupError, "Private keys"):
            self.run_fake(fake)
        self.assertNotIn("ClickHouse native backup", fake.labels)
        self.assert_staging_removed()

    def test_wrong_server_major_is_not_archived(self):
        fake = FakeCommands()
        fake.server_version = b";     Dumped from database version: 15.10\n"
        with self.assertRaisesRegex(MODULE.BackupError, "source must be version 16"):
            self.run_fake(fake)
        self.assertNotIn("Backup encryption", fake.labels)
        self.assert_staging_removed()

    def test_invalid_source_urls_and_limits_fail_without_leaking_password(self):
        for source in (None, "", "mysql://a:secret@db/db", "postgres://a@db/db",
                       "postgres://a:secret@db:0/db", "postgres://a:secret@db:65536/db",
                       "postgres://a:secret@db/db?options=-cfoo", "postgres://a:secret@db/db?sslmode=",
                       "postgres://a:secret@db/db?sslmode=require&sslmode=disable",
                       "postgres://a:secret@db/a/b", "postgres://a:sec%00ret@db/db",
                       "postgres://a:secret%ZZ@db/db", "postgres://a:secret@db/db#fragment",
                       "postgres://a:secret@db/db?connect_timeout=1000"):
            with self.subTest(source=source):
                with self.assertRaises(MODULE.BackupError) as exc:
                    MODULE.source_pg_env(source)
                self.assertNotIn("secret", str(exc.exception))
        for field, value in (("BACKUP_MAX_BYTES", "0"), ("BACKUP_MAX_BYTES", str(17 * 1024 ** 3)),
                             ("BACKUP_COMMAND_TIMEOUT_SECONDS", "3601"),
                             ("CLICKHOUSE_DATABASE", "db;DROP DATABASE db"),
                             ("BACKUP_RECIPIENT_FINGERPRINT", "not-fingerprint")):
            with self.subTest(field=field, value=value):
                with self.assertRaises(MODULE.BackupError):
                    MODULE.Config({**self.env, field: value})

    def test_malformed_receipt_is_not_accepted(self):
        for raw in (b"[]", b"broken-json", b'{"schema":"rbx.plausible.archive-receipt.v1"}'):
            with self.assertRaises(MODULE.BackupError):
                MODULE.validate_archive_receipt(raw, {"sha256": "a" * 64, "size_bytes": 32})

    def test_process_output_size_is_bounded_and_stderr_is_never_exposed(self):
        with self.assertRaisesRegex(MODULE.BackupError, "size limit"):
            MODULE.run_command([sys.executable, "-c", "import sys;sys.stdout.write('x'*4096)"],
                               env={}, timeout=2, label="Bounded command", max_bytes=1024)
        with self.assertRaises(MODULE.BackupError) as exc:
            MODULE.run_command([sys.executable, "-c", "import sys;sys.stderr.write('secret');sys.exit(2)"],
                               env={}, timeout=2, label="Failed command")
        self.assertNotIn("secret", str(exc.exception))

    def test_process_timeout_is_enforced(self):
        with self.assertRaisesRegex(MODULE.BackupError, "time limit"):
            MODULE.run_command([sys.executable, "-c", "import time;time.sleep(5)"],
                               env={}, timeout=0.05, label="Slow command")

    def test_bundle_writer_refuses_unbounded_growth(self):
        stream = io.BytesIO()
        writer = MODULE.LimitedWriter(stream, 4)
        writer.write(b"1234")
        with self.assertRaisesRegex(MODULE.BackupError, "size limit"):
            writer.write(b"5")
        self.assertEqual(stream.getvalue(), b"1234")


if __name__ == "__main__":
    unittest.main()
