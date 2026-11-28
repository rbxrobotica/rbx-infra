"""Synthetic media-copy fixtures only: never access devices, keys or a vault."""
from __future__ import annotations

import contextlib
import copy
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


def load_source(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


capacity = load_source("capacity")
kit = load_source("kit_package")
media = load_source("media_copy")


class MediaCopyTests(unittest.TestCase):
    CLOSED = b"SYNTHETIC CLOSED CIPHERTEXT FIXTURE -- not real encryption\n"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-media-copy-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "state"
        self.mount = self.root / "synthetic-media"
        self.generation = self.root / "closed-generation"
        self.readback = self.root / "readback"
        for folder in (self.base, self.mount, self.generation, self.readback):
            folder.mkdir(mode=0o700)
        proof_dir = self.base / ("capacity-A-" + "a" * 32)
        proof_dir.mkdir(mode=0o700)
        self.proof_path = proof_dir / "status.json"
        self.proof = {
            "role": "Kit A", "phase": "finished",
            "result": "passed_file_capacity_and_integrity_test",
            "only_owned_test_files_removed": True,
            "all_written_pages_nonresident_before_read": True,
            "tested_bytes": 60_000_000_000,
            "counters": {"Data OK": 117_187_500, "Data LOST": 0,
                         "Corrupted": 0, "Slightly changed": 0, "Overwritten": 0},
        }
        self.receipt = {
            "result": "sealed_outer_roundtrip_passed",
            "ciphertext": kit.digest(self.CLOSED),
            "complete_snapshot_authenticated_decryption_tested": False,
        }
        self.write_json(self.proof_path, self.proof)
        self.write_json(self.generation / "seal-receipt.json", self.receipt)
        self.write_private(self.generation / "recovery-kit.tar.gpg", self.CLOSED)
        self.device = self.mount.stat().st_dev
        self.destination_name = "RBX-RECOVERY-20261122-" + self.receipt["ciphertext"]["sha256"][:16]
        self.destination = self.mount / self.destination_name
        patches = (
            mock.patch.object(capacity, "BASE", self.base),
            mock.patch.object(capacity, "device", return_value=(self.mount, self.device)),
            mock.patch.object(capacity, "evict_and_verify", return_value=1),
        )
        self.base_patch, self.device_mock, self.eviction = [p.start() for p in patches]
        for patch in patches:
            self.addCleanup(patch.stop)

    @staticmethod
    def write_private(path, raw):
        path.write_bytes(raw)
        path.chmod(0o600)

    def write_json(self, path, value):
        self.write_private(path, (json.dumps(value) + "\n").encode())

    def publish(self, *, role="A", proof=None, state=None):
        return media.publish(role, self.generation,
                             self.proof_path if proof is None else proof,
                             self.readback if state is None else state)

    def assert_not_promoted(self):
        self.assertFalse((self.readback / "media-copy-receipt.json").exists())

    def test_closed_generation_is_copied_and_exact_ciphertext_readback_is_recorded(self):
        answer = self.publish()
        self.assertEqual(answer["result"], "passed_physical_ciphertext_readback")
        self.assertEqual(answer["role"], "Kit A")
        self.assertEqual(answer["ciphertext"], kit.digest(self.CLOSED))
        self.assertEqual((self.destination / "recovery-kit.tar.gpg").read_bytes(), self.CLOSED)
        self.assertEqual((self.readback / "readback.tar.gpg").read_bytes(), self.CLOSED)
        self.assertEqual(json.loads((self.readback / "media-copy-receipt.json").read_text()), answer)
        self.assertTrue(answer["all_files_flushed_and_nonresident_before_readback"])
        self.assertFalse(answer["complete_restore_from_this_readback"])
        self.assertFalse(answer["direct_thinkcentre_usb_read_or_power_cycle_tested"])
        self.assertEqual(self.eviction.call_count, 4)
        self.assertEqual(self.device_mock.call_count, 2)
        self.assertEqual(set(p.name for p in self.destination.iterdir()),
                         {"recovery-kit.tar.gpg", "seal-receipt.json", "OPEN-FIRST.md", "SHA256SUMS"})
        for line in (self.destination / "SHA256SUMS").read_text().splitlines():
            value, name = line.split("  ")
            self.assertEqual(hashlib.sha256((self.destination / name).read_bytes()).hexdigest(), value)

    def test_all_destination_reads_follow_their_eviction_gate(self):
        real_read = os.read
        gates = set()
        reads = set()
        def evict(fd, length):
            info = os.fstat(fd)
            self.assertEqual(info.st_size, length)
            gates.add(info.st_ino)
            return 1
        def read(fd, length):
            info = os.fstat(fd)
            if info.st_ino in gates:
                reads.add(info.st_ino)
            return real_read(fd, length)
        self.eviction.side_effect = evict
        with mock.patch.object(os, "read", side_effect=read):
            self.publish()
        self.assertEqual(len(gates), 4)
        self.assertEqual(reads, gates)

    def test_capacity_receipt_path_and_role_are_exact(self):
        bad_paths = (
            self.root / "status.json",
            self.base / ("capacity-B-" + "a" * 32) / "status.json",
            self.base / ("capacity-A-" + "A" * 32) / "status.json",
            self.proof_path.with_name("other.json"),
        )
        for path in bad_paths:
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "unexpected_capacity_receipt"):
                self.publish(proof=path)
        with self.assertRaisesRegex(ValueError, "unexpected_capacity_receipt"):
            self.publish(role="B")
        self.device_mock.assert_not_called()
        self.assertEqual(list(self.mount.iterdir()), [])

    def test_capacity_qualification_flags_role_phase_result_and_minimum_are_required(self):
        changes = (
            ("role", "Kit B"), ("result", "running"), ("phase", "f3read"),
            ("only_owned_test_files_removed", False),
            ("only_owned_test_files_removed", "true"),
            ("only_owned_test_files_removed", 1),
            ("all_written_pages_nonresident_before_read", False),
            ("all_written_pages_nonresident_before_read", "true"),
            ("tested_bytes", 59_999_999_999),
            ("tested_bytes", 60_000_000_000.0),
        )
        for key, value in changes:
            with self.subTest(key=key, value=value):
                proof = copy.deepcopy(self.proof); proof[key] = value
                self.write_json(self.proof_path, proof)
                with self.assertRaisesRegex(ValueError, "capacity_qualification_required"):
                    self.publish()
        self.device_mock.assert_not_called()
        self.assertEqual(list(self.mount.iterdir()), [])

    def test_counter_values_require_integers_instead_of_equal_floats_or_booleans(self):
        for name, value in (("Data OK", 117_187_500.0), ("Data LOST", False)):
            with self.subTest(name=name, value=value):
                proof = copy.deepcopy(self.proof)
                proof["counters"][name] = value
                self.write_json(self.proof_path, proof)
                with self.assertRaisesRegex(ValueError, "capacity_counters_not_reconciled"):
                    self.publish()
        self.assertEqual(list(self.mount.iterdir()), [])

    def test_role_b_uses_its_own_qualification(self):
        proof = copy.deepcopy(self.proof); proof["role"] = "Kit B"
        folder = self.base / ("capacity-B-" + "b" * 32); folder.mkdir(mode=0o700)
        path = folder / "status.json"; self.write_json(path, proof)
        answer = self.publish(role="B", proof=path)
        self.assertEqual(answer["role"], "Kit B")
        self.assertEqual((self.destination / "recovery-kit.tar.gpg").read_bytes(), self.CLOSED)

    def test_existing_role_lock_blocks_copy_without_touching_media(self):
        fd = os.open(self.base / "capacity-A.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.publish()
        finally:
            os.close(fd)
        self.device_mock.assert_not_called()
        self.assertEqual(list(self.mount.iterdir()), [])
        self.assertEqual(list(self.readback.iterdir()), [])

    def test_capacity_evidence_hash_uses_the_original_validated_bytes(self):
        expected = hashlib.sha256(self.proof_path.read_bytes()).hexdigest()
        changed = {"result": "synthetic replacement after validation"}
        def evict(fd, length):
            self.write_json(self.proof_path, changed)
            return 1
        self.eviction.side_effect = evict
        answer = self.publish()
        self.assertEqual(answer["capacity_receipt_sha256"], expected)
        self.assertNotEqual(hashlib.sha256(self.proof_path.read_bytes()).hexdigest(), expected)

    def test_every_capacity_counter_must_reconcile(self):
        for name in self.proof["counters"]:
            with self.subTest(name=name):
                proof = copy.deepcopy(self.proof)
                proof["counters"][name] += 1
                self.write_json(self.proof_path, proof)
                with self.assertRaisesRegex(ValueError, "capacity_counters_not_reconciled"):
                    self.publish()
        for change in ("missing", "extra"):
            with self.subTest(change=change):
                proof = copy.deepcopy(self.proof)
                if change == "missing":
                    del proof["counters"]["Data LOST"]
                else:
                    proof["counters"]["unexpected"] = 0
                self.write_json(self.proof_path, proof)
                with self.assertRaisesRegex(ValueError, "capacity_counters_not_reconciled"):
                    self.publish()
        self.device_mock.assert_not_called()

    def test_capacity_bytes_must_reconcile_exact_whole_sectors(self):
        proof = copy.deepcopy(self.proof); proof["tested_bytes"] += 1
        self.write_json(self.proof_path, proof)
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(list(self.mount.iterdir()), [])

    def test_source_ciphertext_hash_length_and_seal_result_must_match(self):
        for change in ("hash", "length", "result", "content"):
            with self.subTest(change=change):
                receipt = copy.deepcopy(self.receipt)
                body = self.CLOSED
                if change == "hash":
                    receipt["ciphertext"]["sha256"] = "0" * 64
                elif change == "length":
                    receipt["ciphertext"]["bytes"] += 1
                elif change == "result":
                    receipt["result"] = "pending"
                else:
                    body = b"changed synthetic closed content"
                self.write_json(self.generation / "seal-receipt.json", receipt)
                self.write_private(self.generation / "recovery-kit.tar.gpg", body)
                with self.assertRaisesRegex(ValueError, "closed_generation_mismatch"):
                    self.publish()
        self.device_mock.assert_not_called()
        self.assertEqual(list(self.mount.iterdir()), [])

    def test_source_ciphertext_symlink_and_permissive_permissions_are_refused(self):
        cipher = self.generation / "recovery-kit.tar.gpg"
        cipher.chmod(0o644)
        with self.assertRaises(ValueError):
            self.publish()
        cipher.chmod(0o600)
        target = self.root / "synthetic-other-ciphertext"
        self.write_private(target, self.CLOSED)
        cipher.unlink(); cipher.symlink_to(target)
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(list(self.mount.iterdir()), [])

    def test_nonempty_or_nonprivate_readback_directory_is_refused(self):
        original = self.readback / "keep.txt"
        original.write_bytes(b"preserve")
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(original.read_bytes(), b"preserve")
        original.unlink()
        self.readback.chmod(0o755)
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(list(self.mount.iterdir()), [])

    def test_readback_directory_symlink_is_refused(self):
        link = self.root / "readback-link"
        link.symlink_to(self.readback, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.publish(state=link)
        self.assertEqual(list(self.mount.iterdir()), [])

    def test_generation_name_collision_preserves_existing_file(self):
        self.destination.write_bytes(b"preexisting file must survive")
        with self.assertRaises(FileExistsError):
            self.publish()
        self.assertEqual(self.destination.read_bytes(), b"preexisting file must survive")
        self.assert_not_promoted()

    def test_generation_directory_collision_preserves_existing_contents(self):
        self.destination.mkdir()
        original = self.destination / "keep.txt"; original.write_bytes(b"original")
        with self.assertRaises(FileExistsError):
            self.publish()
        self.assertEqual(original.read_bytes(), b"original")
        self.assertEqual(set(p.name for p in self.destination.iterdir()), {"keep.txt"})
        self.assert_not_promoted()

    def test_preexisting_sibling_survives_success_and_no_unlink_is_used(self):
        sibling = self.mount / "other-generation"
        sibling.mkdir(); (sibling / "keep").write_bytes(b"original sibling")
        with mock.patch.object(os, "unlink", side_effect=AssertionError("unexpected unlink")), \
             mock.patch.object(os, "remove", side_effect=AssertionError("unexpected remove")), \
             mock.patch.object(os, "rmdir", side_effect=AssertionError("unexpected rmdir")):
            self.publish()
        self.assertEqual((sibling / "keep").read_bytes(), b"original sibling")

    def test_initial_device_mismatch_stops_before_creating_generation(self):
        self.device_mock.return_value = (self.mount, self.device + 1)
        with self.assertRaisesRegex(ValueError, "media_identity_or_free_space_changed"):
            self.publish()
        self.assertEqual(list(self.mount.iterdir()), [])
        self.assert_not_promoted()

    def test_device_disappearing_before_open_preserves_sources(self):
        self.device_mock.side_effect = OSError("synthetic disconnect")
        with self.assertRaises(OSError):
            self.publish()
        self.assertEqual((self.generation / "recovery-kit.tar.gpg").read_bytes(), self.CLOSED)
        self.assertEqual(list(self.mount.iterdir()), [])

    def test_short_read_preserves_written_ciphertext_and_does_not_promote(self):
        real_read = os.read
        target = {"inode": None}
        def evict(fd, length):
            target["inode"] = os.fstat(fd).st_ino
            return 1
        def short(fd, length):
            return b"" if os.fstat(fd).st_ino == target["inode"] else real_read(fd, length)
        self.eviction.side_effect = evict
        with mock.patch.object(os, "read", side_effect=short), \
             mock.patch.object(os, "unlink", side_effect=AssertionError("unexpected unlink")):
            with self.assertRaisesRegex(ValueError, "short_physical_readback"):
                self.publish()
        self.assertEqual((self.destination / "recovery-kit.tar.gpg").read_bytes(), self.CLOSED)
        self.assertEqual(list(self.readback.iterdir()), [])
        self.assert_not_promoted()

    def test_wrong_readback_preserves_written_ciphertext_and_does_not_promote(self):
        real_read = os.read
        target = {"inode": None}
        def evict(fd, length):
            target["inode"] = os.fstat(fd).st_ino
            return 1
        def corrupt(fd, length):
            raw = real_read(fd, length)
            return bytes([raw[0] ^ 1]) + raw[1:] if raw and os.fstat(fd).st_ino == target["inode"] else raw
        self.eviction.side_effect = evict
        with mock.patch.object(os, "read", side_effect=corrupt):
            with self.assertRaisesRegex(ValueError, "physical_readback_mismatch"):
                self.publish()
        self.assertEqual((self.destination / "recovery-kit.tar.gpg").read_bytes(), self.CLOSED)
        self.assert_not_promoted()

    def test_later_eviction_failure_keeps_partial_outputs_and_verified_ciphertext(self):
        self.eviction.side_effect = [1, ValueError("synthetic_eviction_failure")]
        with mock.patch.object(os, "unlink", side_effect=AssertionError("unexpected unlink")):
            with self.assertRaisesRegex(ValueError, "synthetic_eviction_failure"):
                self.publish()
        self.assertEqual((self.destination / "recovery-kit.tar.gpg").read_bytes(), self.CLOSED)
        self.assertTrue((self.destination / "seal-receipt.json").is_file())
        self.assertEqual((self.readback / "readback.tar.gpg").read_bytes(), self.CLOSED)
        self.assert_not_promoted()

    def test_final_identity_mismatch_preserves_copy_but_does_not_promote(self):
        self.device_mock.side_effect = [(self.mount, self.device), (self.mount, self.device + 1)]
        with self.assertRaisesRegex(ValueError, "media_identity_changed_after_readback"):
            self.publish()
        self.assertEqual((self.destination / "recovery-kit.tar.gpg").read_bytes(), self.CLOSED)
        self.assertEqual((self.readback / "readback.tar.gpg").read_bytes(), self.CLOSED)
        self.assert_not_promoted()

    def test_named_ciphertext_replacement_during_readback_is_preserved_and_rejected(self):
        held = self.root / "held-original-ciphertext"
        def evict(fd, length):
            if self.eviction.call_count == 1:
                cipher = self.destination / "recovery-kit.tar.gpg"
                cipher.rename(held)
                self.write_private(cipher, b"synthetic concurrent replacement")
            return 1
        self.eviction.side_effect = evict
        with mock.patch.object(os, "unlink", side_effect=AssertionError("unexpected unlink")):
            with self.assertRaisesRegex(ValueError, "named_media_file_changed"):
                self.publish()
        self.assertEqual(held.read_bytes(), self.CLOSED)
        self.assertEqual((self.destination / "recovery-kit.tar.gpg").read_bytes(), b"synthetic concurrent replacement")
        self.assert_not_promoted()

    def test_late_ciphertext_replacement_is_caught_by_final_generation_check(self):
        held = self.root / "held-late-original"
        def evict(fd, length):
            if self.eviction.call_count == 2:
                cipher = self.destination / "recovery-kit.tar.gpg"
                cipher.rename(held)
                self.write_private(cipher, b"synthetic late replacement")
            return 1
        self.eviction.side_effect = evict
        with self.assertRaisesRegex(ValueError, "named_media_file_changed"):
            self.publish()
        self.assertEqual(held.read_bytes(), self.CLOSED)
        self.assertEqual((self.readback / "readback.tar.gpg").read_bytes(), self.CLOSED)
        self.assertEqual((self.destination / "recovery-kit.tar.gpg").read_bytes(), b"synthetic late replacement")
        self.assert_not_promoted()

    def test_new_generation_neighbor_is_preserved_and_stops_promotion(self):
        def evict(fd, length):
            if self.eviction.call_count == 4:
                (self.destination / "unexpected-neighbor").write_bytes(b"preserve unexpected data")
            return 1
        self.eviction.side_effect = evict
        with self.assertRaisesRegex(ValueError, "generation_members_changed"):
            self.publish()
        self.assertEqual((self.destination / "unexpected-neighbor").read_bytes(), b"preserve unexpected data")
        self.assert_not_promoted()

    def test_named_generation_replacement_does_not_redirect_open_directory(self):
        held = self.mount / "held-original-generation"
        def evict(fd, length):
            if self.eviction.call_count == 1:
                self.destination.rename(held)
                self.destination.mkdir()
                (self.destination / "neighbor").write_bytes(b"replacement directory data")
            return 1
        self.eviction.side_effect = evict
        with self.assertRaisesRegex(ValueError, "named_generation_changed"):
            self.publish()
        self.assertEqual((held / "recovery-kit.tar.gpg").read_bytes(), self.CLOSED)
        self.assertEqual((self.destination / "neighbor").read_bytes(), b"replacement directory data")
        self.assert_not_promoted()

    def test_concurrent_hardlink_is_preserved_and_stops_promotion(self):
        link = self.root / "concurrent-hardlink"
        def evict(fd, length):
            if self.eviction.call_count == 1:
                os.link(self.destination / "recovery-kit.tar.gpg", link)
            return 1
        self.eviction.side_effect = evict
        with self.assertRaisesRegex(ValueError, "named_media_file_changed"):
            self.publish()
        self.assertEqual(link.read_bytes(), self.CLOSED)
        self.assertEqual((self.destination / "recovery-kit.tar.gpg").read_bytes(), self.CLOSED)
        self.assert_not_promoted()

    def test_open_media_directory_remains_pinned_after_path_replacement(self):
        moved = self.root / "original-media"
        def evict(fd, length):
            if not moved.exists():
                self.mount.rename(moved)
                self.mount.mkdir()
                (self.mount / "neighbor").write_bytes(b"new path occupant")
            return 1
        self.eviction.side_effect = evict
        self.publish()
        self.assertEqual((moved / self.destination_name / "recovery-kit.tar.gpg").read_bytes(), self.CLOSED)
        self.assertEqual((self.mount / "neighbor").read_bytes(), b"new path occupant")
        self.assertEqual(set(p.name for p in self.mount.iterdir()), {"neighbor"})


class GpgDiagnosticTests(unittest.TestCase):
    SENTINEL = b"SYNTHETIC_PRIVATE_SENTINEL_DO_NOT_LOG"

    def test_diagnostic_contains_only_allowlisted_tags_and_uint32_error_codes(self):
        stderr = b"\n".join((
            b"human diagnostic " + self.SENTINEL,
            b"[GNUPG:] ERROR decrypt 12345",
            b"[GNUPG:] FAILURE gpg-exit 99",
            b"[GNUPG:] ERROR decrypt 12345",
            b"[GNUPG:] ERROR arbitrary-operation 4294967295",
            b"[GNUPG:] ERROR overflow 4294967296",
            b"[GNUPG:] ERROR oversized 99999999999",
            b"[GNUPG:] ERROR negative -1",
            b"[GNUPG:] ERROR extra 42 " + self.SENTINEL,
            b"[GNUPG:] ERROR /private/path 55",
            b"[GNUPG:] UNKNOWN_TAG " + self.SENTINEL,
            b"[GNUPG:] NO_SECKEY " + self.SENTINEL,
            b"[GNUPG:] DECRYPTION_FAILED",
        ))
        result = subprocess.CompletedProcess([], 2, self.SENTINEL, stderr)
        out = io.StringIO(); err = io.StringIO()
        with mock.patch.object(subprocess, "run", return_value=result), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(kit.GpgFailure) as caught:
                kit.gpg(Path("/synthetic/unused"), ["--decrypt"], b"synthetic ciphertext")
        self.assertEqual(str(caught.exception), "gpg_operation_failed")
        self.assertEqual(caught.exception.diagnostic, {
            "status_tags": ["DECRYPTION_FAILED", "ERROR", "FAILURE", "NO_SECKEY"],
            "process_exit": 2, "numeric_error_codes": [99, 12345, 4294967295],
        })
        serialized = json.dumps(caught.exception.diagnostic)
        self.assertNotIn(self.SENTINEL.decode(), serialized)
        self.assertNotIn("private", serialized)
        self.assertNotIn("UNKNOWN_TAG", serialized)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
