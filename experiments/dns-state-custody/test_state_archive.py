"""Offline controls use invented states, never provider data or real keys."""

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import state_archive as sut


MARKER = "synthetic-secret-do-not-emit"


def state(zones, serial=2, lineage="fixture", suffix=""):
    return {"version": 4, "serial": serial, "lineage": lineage,
            "resources": [{"mode": "managed", "type": "powerdns_zone", "name": zone,
                           "instances": [{"attributes": {"id": zone + suffix, "name": zone + ".",
                                                           "fixture_secret": MARKER}}]}
                          for zone in sorted(zones)]}


def snap(value):
    raw = sut.canonical(value)
    return sut.Snapshot({"bytes": len(raw), "sha256": sut.digest(raw)}, raw)


def repack(raw, edit):
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        items = [(x.name, archive.extractfile(x).read()) for x in archive]
    items = edit(items)
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w") as archive:
        for name, body in items:
            member = tarfile.TarInfo(name)
            member.size = len(body)
            archive.addfile(member, io.BytesIO(body))
    return result.getvalue()


class ArchiveControls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.principal = self.root / "main" / "terraform.tfstate"
        self.historical = self.root / "old" / "terraform.tfstate"
        for path, value in [(self.principal, state(sut.ZONES)),
                            (self.historical, state({"rbxsystems.ch"}, serial=1))]:
            path.parent.mkdir()
            for target in (path, Path(str(path) + ".backup")):
                target.write_bytes(sut.canonical(value))
                target.chmod(0o600)

    def archive(self):
        snapshots, _ = sut.capture(self.principal, self.historical)
        return sut.make_archive(snapshots)

    def test_aggregate_candidate_does_not_emit_state_values(self):
        snapshots, report = sut.capture(self.principal, self.historical)
        self.assertTrue(report["principal_canonical_candidate_supported"])
        self.assertFalse(report["all_record_values_live_reconciled"])
        self.assertNotIn(MARKER, json.dumps(report))
        self.assertNotIn(MARKER, repr(snapshots))

    def test_lineage_serial_or_binding_conflict_blocks_candidate(self):
        main = snap(state(sut.ZONES))
        for old in [state({"rbxsystems.ch"}, 1, "other"),
                    state({"rbxsystems.ch"}, 2),
                    state({"rbxsystems.ch"}, 1, suffix="different"), state(set(), 1)]:
            with self.subTest(old=old["lineage"]):
                self.assertFalse(sut.compare(main, snap(old))["principal_canonical_candidate_supported"])

    def test_missing_managed_binding_blocks_candidate(self):
        old = state({"rbxsystems.ch", "missing.example"}, 1)
        self.assertFalse(sut.compare(snap(state(sut.ZONES)), snap(old))["principal_canonical_candidate_supported"])

    def test_duplicate_bindings_are_rejected(self):
        value = state(sut.ZONES)
        value["resources"].append(value["resources"][0])
        with self.assertRaisesRegex(sut.SafeError, "duplicate_state_binding"):
            sut.compare(snap(value), snap(state({"rbxsystems.ch"}, 1)))

    def test_source_lock_blocks_capture(self):
        (self.principal.parent / ".terraform.tfstate.lock.info").write_text("fixture")
        with self.assertRaisesRegex(sut.SafeError, "source_lock_present"):
            sut.capture(self.principal, self.historical)

    def test_source_symlink_is_rejected(self):
        target = self.principal.with_name("terraform.tfstate.link")
        target.symlink_to(self.principal)
        with self.assertRaisesRegex(sut.SafeError, "source_unavailable_or_symlink"):
            sut.snapshot(target)

    def test_source_unsupported_format_is_rejected(self):
        self.principal.write_text('{"version":3}')
        with self.assertRaisesRegex(sut.SafeError, "unsupported_state_format"):
            sut.snapshot(self.principal)

    def test_exact_four_files_are_restored_in_memory(self):
        raw, _, expected = self.archive()
        before = set(self.root.rglob("*"))
        report = sut.validate_archive(raw, expected)
        self.assertEqual(report["files_restored_in_memory"], 4)
        self.assertTrue(report["all_source_checksums_match"])
        self.assertFalse(report["plaintext_files_written"])
        self.assertEqual(set(self.root.rglob("*")), before)

    def test_external_manifest_digest_is_required(self):
        raw, _, _ = self.archive()
        with self.assertRaisesRegex(sut.SafeError, "manifest_or_members_mismatch"):
            sut.validate_archive(raw, "0" * 64)

    def test_corrupted_payload_fails_restore(self):
        raw, _, expected = self.archive()
        raw = repack(raw, lambda items: [(n, b + b" " if n == sut.LABELS[0] else b) for n, b in items])
        with self.assertRaisesRegex(sut.SafeError, "checksum_mismatch"):
            sut.validate_archive(raw, expected)

    def test_traversal_duplicate_and_missing_members_are_rejected(self):
        raw, _, expected = self.archive()
        edits = [lambda x: x + [("../escape", b"fixture")],
                 lambda x: x + [x[0]], lambda x: x[:-1]]
        for edit in edits:
            with self.subTest(edit=edit), self.assertRaises(sut.SafeError):
                sut.validate_archive(repack(raw, edit), expected)

    def test_ciphertext_output_is_private_and_never_overwritten(self):
        target = self.root / "private" / "fixture.tar.gpg"
        sut.write_exclusive(target, b"fixture-ciphertext")
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(target.parent.stat().st_mode & 0o777, 0o700)
        with self.assertRaisesRegex(sut.SafeError, "already_exists"):
            sut.write_exclusive(target, b"replacement")
        self.assertEqual(target.read_bytes(), b"fixture-ciphertext")

    def test_nonprivate_destination_is_rejected(self):
        target = self.root / "public" / "fixture.tar.gpg"
        target.parent.mkdir(mode=0o755)
        with self.assertRaisesRegex(sut.SafeError, "directory_not_private_owned"):
            sut.write_exclusive(target, b"fixture")

    def test_encrypt_failure_does_not_expose_stderr_or_payload(self):
        result = subprocess.CompletedProcess([], 2, stdout=b"", stderr=MARKER.encode())
        with patch.object(sut.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(sut.SafeError, "gpg_encryption_failed"):
                sut.encrypt_archive(MARKER.encode(), "A" * 40)

    def test_recipient_must_be_full_fingerprint(self):
        with patch.object(sut.subprocess, "run") as run:
            with self.assertRaisesRegex(sut.SafeError, "full_fingerprint"):
                sut.encrypt_archive(b"fixture", "user@example.com")
            run.assert_not_called()

    def test_failed_cli_does_not_emit_arbitrary_error_values(self):
        out = io.StringIO()
        with patch.object(sys, "argv", ["state_archive.py", "inspect", "--principal", str(self.principal),
                                        "--historical", str(self.historical)]), \
                patch.object(sut, "capture", side_effect=ValueError(MARKER)), contextlib.redirect_stdout(out):
            self.assertEqual(sut.main(), 1)
        self.assertNotIn(MARKER, out.getvalue())
        self.assertEqual(json.loads(out.getvalue())["error_type"], "ValueError")

    def test_verification_never_follows_symlink(self):
        target = self.root / "fixture.tar.gpg"
        target.symlink_to(self.principal)
        with self.assertRaisesRegex(sut.SafeError, "unavailable_or_symlink"):
            sut.verify(target, "0" * 64)

    def test_source_change_after_encryption_prevents_commit(self):
        target = self.root / "private" / "fixture.tar.gpg"

        def changed_source(*args):
            self.principal.write_bytes(self.principal.read_bytes() + b" ")
            return b"fixture-ciphertext"

        out = io.StringIO()
        argv = ["state_archive.py", "backup", "--principal", str(self.principal),
                "--historical", str(self.historical), "--output", str(target), "--recipient", "A" * 40]
        with patch.object(sys, "argv", argv), patch.object(sut, "encrypt_archive", side_effect=changed_source), \
                contextlib.redirect_stdout(out):
            self.assertEqual(sut.main(), 1)
        self.assertEqual(json.loads(out.getvalue())["error"], "source_changed_before_backup_commit")
        self.assertFalse(target.exists())

    def test_decrypted_archive_is_verified_without_extracting(self):
        raw, _, expected = self.archive()
        target = self.root / "fixture.tar.gpg"
        target.write_bytes(b"fixture-ciphertext")
        result = subprocess.CompletedProcess([], 0, stdout=raw, stderr=MARKER.encode())
        with patch.object(sut.subprocess, "run", return_value=result):
            report = sut.verify(target, expected)
        self.assertEqual(report["files_restored_in_memory"], 4)
        self.assertNotIn(MARKER, json.dumps(report))


if __name__ == "__main__":
    unittest.main()
