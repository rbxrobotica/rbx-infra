"""Synthetic capsule tests: no keys, vaults, media, pinentry or real GPG calls."""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


_SPEC = importlib.util.spec_from_file_location(
    "offline_kits_package", Path(__file__).with_name("kit_package.py")
)
assert _SPEC is not None and _SPEC.loader is not None
kit = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = kit
_SPEC.loader.exec_module(kit)


def manifest_for(files):
    return {
        "schema": kit.SCHEMA,
        "store_head": "b" * 40,
        "store_commits": 1,
        "current_entries": sum(name.startswith("current/") and name.endswith(".gpg") for name in files),
        "historical_ciphertext_blobs": sum(name.startswith("history/") and name.endswith(".gpg") for name in files),
        "canary": kit.digest(b"synthetic plaintext canary"),
        "files": {name: kit.digest(value) for name, value in files.items()},
    }


def tar_bytes(entries, manifest=None):
    """Build deliberately untrusted archives without invoking extraction."""
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for entry in entries:
            name, raw, *options = entry
            info = tarfile.TarInfo(name)
            info.mode = 0o600
            info.size = len(raw)
            for key, value in (options[0] if options else {}).items():
                setattr(info, key, value)
            archive.addfile(info, io.BytesIO(raw) if info.isfile() else None)
        if manifest is not None:
            raw = json.dumps(manifest).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    return stream.getvalue()


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.files = {
            "private-key.asc": b"synthetic protected key fixture; not a key",
            "public-certificate.gpg": b"synthetic public certificate fixture",
            "recovery-canary.gpg": b"synthetic encrypted canary fixture",
            "password-store.bundle": b"synthetic encrypted-entry git bundle fixture",
            "RECOVERY.md": b"# Synthetic recovery instructions\n",
            "current/.gpg-id": b"SYNTHETIC-ID\n",
            "current/folder/entry.gpg": b"synthetic current ciphertext",
            "history/" + "a" * 40 + ".gpg": b"synthetic historical ciphertext",
        }
        self.manifest = manifest_for(self.files)

    def rejection(self, raw):
        with self.assertRaises((ValueError, tarfile.TarError, KeyError, TypeError, AttributeError)):
            kit.unpack(raw)

    def test_archive_roundtrip_preserves_exact_members_bytes_and_manifest(self):
        before_files = copy.deepcopy(self.files)
        before_manifest = copy.deepcopy(self.manifest)
        files, manifest = kit.unpack(kit.archive(self.files, self.manifest))
        self.assertEqual(files, before_files)
        self.assertEqual(manifest, before_manifest)
        self.assertEqual(self.files, before_files)
        self.assertEqual(self.manifest, before_manifest)

    def test_allowed_unicode_and_long_names_roundtrip_without_pax(self):
        for name in ("current/café.gpg", "current/" + "long-folder/" * 12 + "entry.gpg"):
            with self.subTest(name=name):
                self.assertTrue(kit.allowed_member(name))
                files = {**self.files, name: b"synthetic ciphertext"}
                restored, manifest = kit.unpack(kit.archive(files, manifest_for(files)))
                self.assertEqual(restored, files)
                self.assertEqual(manifest, manifest_for(files))

    def test_allowed_names_are_exact_closed_namespaces(self):
        for name in self.files:
            with self.subTest(name=name):
                self.assertTrue(kit.allowed_member(name))
        for name in (
            "unrelated.txt", "other/entry.gpg", "private-key.asc.backup",
            "manifest.json", "history/" + "A" * 40 + ".gpg",
            "history/" + "a" * 39 + ".gpg", "history/" + "g" * 40 + ".gpg",
            "current/plaintext.txt", "current/.git/config", "history/.gpg-id",
        ):
            with self.subTest(name=name):
                self.assertFalse(kit.allowed_member(name))

    def test_noncanonical_names_are_refused(self):
        for name in (
            "", "/current/a.gpg", "../a.gpg", "current/../a.gpg",
            "current/./a.gpg", "current//a.gpg", "current/a.gpg/",
            "current\\a.gpg", "current/a\x00.gpg", "current/a\n.gpg",
            "current/a\r.gpg", "current/a\t.gpg",
        ):
            with self.subTest(name=name):
                self.assertFalse(kit.allowed_member(name))

    def test_archive_refuses_unknown_namespace_before_serialization(self):
        files = {"other/secret.txt": b"synthetic sentinel"}
        with self.assertRaises(ValueError):
            kit.archive(files, manifest_for(files))

    def test_archive_refuses_manifest_schema_member_or_digest_mismatch(self):
        for change in ("schema", "missing", "extra", "digest", "size", "digest_extra"):
            with self.subTest(change=change):
                manifest = copy.deepcopy(self.manifest)
                if change == "schema":
                    manifest["schema"] = "unreviewed.v2"
                elif change == "missing":
                    del manifest["files"]["RECOVERY.md"]
                elif change == "extra":
                    manifest["files"]["current/extra.gpg"] = kit.digest(b"extra")
                elif change == "digest":
                    manifest["files"]["RECOVERY.md"]["sha256"] = "0" * 64
                elif change == "size":
                    manifest["files"]["RECOVERY.md"]["bytes"] += 1
                else:
                    manifest["files"]["RECOVERY.md"]["unexpected"] = "metadata"
                with self.assertRaises(ValueError):
                    kit.archive(self.files, manifest)

    def test_archive_refuses_empty_or_oversized_members(self):
        for body in (b"", b"x" * 1025):
            with self.subTest(length=len(body)), mock.patch.object(kit, "MAX_MEMBER", 1024):
                files = {"RECOVERY.md": body}
                with self.assertRaises(ValueError):
                    kit.archive(files, manifest_for(files))

    def test_archive_member_and_byte_limits_apply(self):
        raw = kit.archive(self.files, self.manifest)
        with mock.patch.object(kit, "MAX_MEMBERS", len(self.files)), self.assertRaises(ValueError):
            kit.archive(self.files, self.manifest)
        with mock.patch.object(kit, "MAX_ARCHIVE", len(raw) - 1), self.assertRaises(ValueError):
            kit.archive(self.files, self.manifest)

    def test_unpack_refuses_empty_oversized_and_truncated_input(self):
        self.rejection(b"")
        raw = kit.archive(self.files, self.manifest)
        with mock.patch.object(kit, "MAX_ARCHIVE", len(raw) - 1):
            self.rejection(raw)
        self.rejection(raw[:600])

    def test_unpack_refuses_unexpected_namespace_and_traversal(self):
        for name in ("other/file.gpg", "/current/a.gpg", "current/../a.gpg", "current/./a.gpg", "current//a.gpg", "current\\a.gpg"):
            with self.subTest(name=name):
                files = {name: b"synthetic"}
                self.rejection(tar_bytes(list(files.items()), manifest_for(files)))

    def test_unpack_refuses_all_nonregular_member_types(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE):
            with self.subTest(kind=kind):
                entries = [("RECOVERY.md", b"", {"type": kind, "linkname": "/outside/sentinel"})]
                self.rejection(tar_bytes(entries, manifest_for({"RECOVERY.md": b""})))

    def test_unpack_refuses_sparse_and_pax_members(self):
        entries = [("RECOVERY.md", b"synthetic", {"pax_headers": {"comment": "unreviewed"}})]
        self.rejection(tar_bytes(entries, manifest_for({"RECOVERY.md": b"synthetic"})))
        entries = [("RECOVERY.md", b"", {"type": tarfile.GNUTYPE_SPARSE})]
        self.rejection(tar_bytes(entries, manifest_for({"RECOVERY.md": b""})))

    def test_unpack_refuses_duplicate_payload_and_manifest_members(self):
        raw = b"synthetic"
        manifest = manifest_for({"RECOVERY.md": raw})
        self.rejection(tar_bytes([("RECOVERY.md", raw), ("RECOVERY.md", raw)], manifest))
        self.rejection(tar_bytes([("RECOVERY.md", raw), ("manifest.json", json.dumps(manifest).encode())], manifest))

    def test_unpack_refuses_missing_manifest_and_unlisted_or_missing_payload(self):
        self.rejection(tar_bytes([("RECOVERY.md", b"one"), ("current/a.gpg", b"two")]))
        manifest = manifest_for({"RECOVERY.md": b"one"})
        self.rejection(tar_bytes([("RECOVERY.md", b"one"), ("current/a.gpg", b"two")], manifest))
        manifest = manifest_for({"RECOVERY.md": b"one", "current/a.gpg": b"two"})
        self.rejection(tar_bytes([("RECOVERY.md", b"one")], manifest))

    def test_unpack_refuses_wrong_schema_payload_hash_and_payload_size(self):
        for change in ("schema", "hash", "size"):
            with self.subTest(change=change):
                manifest = manifest_for({"RECOVERY.md": b"synthetic"})
                if change == "schema":
                    manifest["schema"] = "other"
                elif change == "hash":
                    manifest["files"]["RECOVERY.md"]["sha256"] = "f" * 64
                else:
                    manifest["files"]["RECOVERY.md"]["bytes"] += 1
                self.rejection(tar_bytes([("RECOVERY.md", b"synthetic")], manifest))

    def test_unpack_refuses_malformed_manifest_shapes(self):
        for manifest in (None, [], "text", {}, {"schema": kit.SCHEMA}, {"schema": kit.SCHEMA, "files": []}):
            with self.subTest(manifest=manifest):
                raw = json.dumps(manifest).encode()
                self.rejection(tar_bytes([("RECOVERY.md", b"synthetic"), ("manifest.json", raw)]))

    def test_required_members_cannot_be_omitted_from_a_consistent_manifest(self):
        for name in ("private-key.asc", "public-certificate.gpg", "recovery-canary.gpg", "password-store.bundle", "RECOVERY.md", "current/.gpg-id"):
            with self.subTest(name=name):
                files = {n: body for n, body in self.files.items() if n != name}
                manifest = manifest_for(files)
                with self.assertRaisesRegex(ValueError, "incomplete_recovery_manifest"):
                    kit.archive(files, manifest)
                with self.assertRaisesRegex(ValueError, "incomplete_recovery_manifest"):
                    kit.unpack(tar_bytes(list(files.items()), manifest))

    def test_manifest_requires_current_and_historical_ciphertext(self):
        for prefix in ("current/", "history/"):
            with self.subTest(prefix=prefix):
                files = {n: body for n, body in self.files.items()
                         if not (n.startswith(prefix) and n.endswith(".gpg"))}
                manifest = manifest_for(files)
                with self.assertRaisesRegex(ValueError, "recovery_scope_count_mismatch"):
                    kit.archive(files, manifest)

    def test_manifest_head_commits_counts_and_canary_are_validated(self):
        changes = (
            ("store_head", "f" * 39), ("store_head", "F" * 40),
            ("store_head", "../synthetic"), ("store_commits", 0),
            ("store_commits", True), ("store_commits", "1"),
            ("current_entries", 0), ("current_entries", 2),
            ("current_entries", True), ("historical_ciphertext_blobs", 0),
            ("historical_ciphertext_blobs", 2),
            ("canary", {"bytes": 0, "sha256": "a" * 64}),
            ("canary", {"bytes": True, "sha256": "a" * 64}),
            ("canary", {"bytes": 1, "sha256": "A" * 64}),
            ("canary", {"bytes": 1, "sha256": "a" * 63}),
        )
        for field, value in changes:
            with self.subTest(field=field, value=value):
                manifest = copy.deepcopy(self.manifest)
                manifest[field] = value
                with self.assertRaises(ValueError):
                    kit.archive(self.files, manifest)
                with self.assertRaises(ValueError):
                    kit.unpack(tar_bytes(list(self.files.items()), manifest))

    def test_unpack_member_and_byte_limits_apply(self):
        raw = kit.archive(self.files, self.manifest)
        with mock.patch.object(kit, "MAX_MEMBERS", len(self.files)):
            self.rejection(raw)
        body = b"x" * 1025
        with mock.patch.object(kit, "MAX_MEMBER", 1024):
            self.rejection(tar_bytes([("RECOVERY.md", body)], manifest_for({"RECOVERY.md": body})))

    def test_unpack_reads_in_memory_without_filesystem_extraction(self):
        raw = kit.archive(self.files, self.manifest)
        with mock.patch.object(tarfile.TarFile, "extract", side_effect=AssertionError("filesystem extraction")), \
             mock.patch.object(tarfile.TarFile, "extractall", side_effect=AssertionError("filesystem extraction")), \
             mock.patch.object(kit.os, "open", side_effect=AssertionError("filesystem write/read")):
            self.assertEqual(kit.unpack(raw)[0], self.files)


class OwnedReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-kit-read-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "synthetic.bin"
        self.path.write_bytes(b"synthetic fixture")
        self.path.chmod(0o600)

    def test_owned_private_regular_file_is_read_exactly(self):
        self.assertEqual(kit.read_owned(self.path), b"synthetic fixture")

    def test_final_and_parent_symlinks_are_refused(self):
        alias = self.root / "alias.bin"
        alias.symlink_to(self.path)
        with self.assertRaises(ValueError):
            kit.read_owned(alias)
        folder = self.root / "folder"; folder.mkdir()
        target = folder / "file"; target.write_bytes(b"synthetic"); target.chmod(0o600)
        link = self.root / "linked-folder"; link.symlink_to(folder, target_is_directory=True)
        with self.assertRaises(ValueError):
            kit.read_owned(link / "file")

    def test_directory_and_nonowned_files_are_refused(self):
        with self.assertRaises(ValueError):
            kit.read_owned(self.root)
        with mock.patch.object(os, "getuid", return_value=os.getuid() + 1), self.assertRaises(ValueError):
            kit.read_owned(self.path)

    def test_private_mode_is_enforced_but_explicit_ciphertext_mode_is_allowed(self):
        self.path.chmod(0o644)
        with self.assertRaises(ValueError):
            kit.read_owned(self.path)
        self.assertEqual(kit.read_owned(self.path, private=False), b"synthetic fixture")

    def test_empty_and_oversized_files_are_refused(self):
        with self.assertRaises(ValueError):
            kit.read_owned(self.path, limit=3)
        self.path.write_bytes(b"")
        with self.assertRaises(ValueError):
            kit.read_owned(self.path)

    def test_short_read_is_refused(self):
        with mock.patch.object(os, "read", return_value=b""), self.assertRaisesRegex(ValueError, "short_source"):
            kit.read_owned(self.path)

    def test_modification_during_read_is_refused(self):
        real_read = os.read
        def changed(fd, amount):
            raw = real_read(fd, amount)
            self.path.write_bytes(b"replacement")
            return raw
        with mock.patch.object(os, "read", side_effect=changed), self.assertRaisesRegex(ValueError, "source_changed"):
            kit.read_owned(self.path)

    def test_write_new_preserves_existing_file_and_symlink_target(self):
        with self.assertRaises(FileExistsError):
            kit.write_new(self.path, b"overwrite")
        self.assertEqual(self.path.read_bytes(), b"synthetic fixture")
        alias = self.root / "alias"; alias.symlink_to(self.path)
        with self.assertRaises(OSError):
            kit.write_new(alias, b"overwrite")
        self.assertEqual(self.path.read_bytes(), b"synthetic fixture")


class GpgBoundaryTests(unittest.TestCase):
    SENTINEL = b"SYNTHETIC_PRIVATE_SENTINEL_DO_NOT_LOG"
    GOOD = b"[GNUPG:] DECRYPTION_OKAY\n[GNUPG:] GOODMDC\n[GNUPG:] END_DECRYPTION\n"

    def result(self, code=0, statuses=None):
        return subprocess.CompletedProcess([], code, self.SENTINEL, self.GOOD if statuses is None else statuses)

    def test_authenticated_decrypt_requires_each_positive_status(self):
        for missing in (b"DECRYPTION_OKAY", b"GOODMDC", b"END_DECRYPTION"):
            statuses = b"\n".join(line for line in self.GOOD.splitlines() if missing not in line)
            with self.subTest(missing=missing), mock.patch.object(subprocess, "run", return_value=self.result(statuses=statuses)):
                with self.assertRaisesRegex(ValueError, "authenticated_decryption_required"):
                    kit.gpg(Path("/synthetic/unused"), ["--decrypt"], b"fake ciphertext")

    def test_failure_statuses_and_nonzero_exit_discard_stdout_without_logging(self):
        for failure in (b"BADMDC", b"DECRYPTION_FAILED", b"ERROR", b"FAILURE", b"NO_SECKEY", None):
            statuses = self.GOOD + (b"[GNUPG:] " + failure + b" " + self.SENTINEL if failure else b"")
            result = self.result(code=1 if failure is None else 0, statuses=statuses)
            with self.subTest(failure=failure), mock.patch.object(subprocess, "run", return_value=result):
                out = io.StringIO(); err = io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    with self.assertRaisesRegex(ValueError, "^gpg_operation_failed$") as caught:
                        kit.gpg(Path("/synthetic/unused"), ["--decrypt"], b"fake ciphertext")
                self.assertNotIn(self.SENTINEL.decode(), str(caught.exception))
                self.assertEqual(out.getvalue(), "")
                self.assertEqual(err.getvalue(), "")

    def test_phrase_is_passed_only_by_fd_and_remote_key_retrieval_is_disabled(self):
        phrase = b"synthetic outer phrase 12345"
        captured = {}
        def fake_run(args, **kwargs):
            captured.update(args=args, kwargs=kwargs)
            fd = kwargs["pass_fds"][0]
            captured["phrase"] = os.read(fd, 2048)
            return self.result()
        with mock.patch.object(subprocess, "run", side_effect=fake_run):
            raw, statuses = kit.gpg(Path("/synthetic/unused"), ["--decrypt"], b"fake ciphertext", phrase)
        self.assertEqual(raw, self.SENTINEL)
        self.assertIn("GOODMDC", statuses)
        args = captured["args"]
        self.assertEqual(args[0], "/usr/bin/gpg")
        for flag in ("--no-options", "--batch", "--no-tty", "--disable-dirmngr", "--no-auto-key-retrieve", "--no-auto-key-import", "--no-symkey-cache"):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("--auto-key-locate") + 1], "clear")
        self.assertEqual(args[args.index("--pinentry-mode") + 1], "loopback")
        self.assertEqual(captured["phrase"], phrase + b"\n")
        self.assertNotIn(phrase.decode(), " ".join(args))
        self.assertEqual(captured["kwargs"]["input"], b"fake ciphertext")
        self.assertNotIn("shell", captured["kwargs"])
        self.assertTrue(captured["kwargs"]["capture_output"])
        with self.assertRaises(OSError):
            os.fstat(captured["kwargs"]["pass_fds"][0])

    def test_phrase_format_rejected_before_subprocess(self):
        for phrase in (b"short", b"a" * 1025, b"synthetic phrase\nextra", b"synthetic phrase\rextra"):
            with self.subTest(phrase_length=len(phrase)), mock.patch.object(subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "invalid_outer_phrase_format"):
                    kit.gpg(Path("/synthetic/unused"), ["--decrypt"], phrase=phrase)
                run.assert_not_called()

    def test_timeout_closes_phrase_fd_and_does_not_log_captured_data(self):
        captured = {}
        def timeout(args, **kwargs):
            captured["fd"] = kwargs["pass_fds"][0]
            raise subprocess.TimeoutExpired(args, 1, output=self.SENTINEL, stderr=self.SENTINEL)
        out = io.StringIO(); err = io.StringIO()
        with mock.patch.object(subprocess, "run", side_effect=timeout), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(subprocess.TimeoutExpired):
                kit.gpg(Path("/synthetic/unused"), ["--decrypt"], phrase=b"synthetic phrase 123456")
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "")
        with self.assertRaises(OSError):
            os.fstat(captured["fd"])

    def test_checked_subprocess_failure_has_fixed_nonsensitive_error(self):
        result = subprocess.CompletedProcess([], 1, self.SENTINEL, self.SENTINEL)
        with mock.patch.object(subprocess, "run", return_value=result):
            with self.assertRaisesRegex(ValueError, "^subprocess_failed$") as caught:
                kit.checked(["/synthetic/never-executed"], self.SENTINEL)
        self.assertNotIn(self.SENTINEL.decode(), str(caught.exception))


if __name__ == "__main__":
    unittest.main()
