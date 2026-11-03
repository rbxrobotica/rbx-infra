"""Recovery and failure contracts for synthetic-only, local fixture artifacts."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from resilience import local_fixture as fixture


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rbx-fixture-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = self.base / "source"
        self.manifest = fixture.create_fixture(self.source)
        self.bundle = self.base / "capture.tar"
        fixture.make_bundle(self.source, self.manifest, self.bundle)
        self.expected = fixture.digest(fixture.canonical(self.manifest))
        self.destination = self.base / "restored"

    def rewritten(self, changes=None, extra=None):
        contents = {name: (self.source / name).read_bytes() for name in fixture.FILES}
        contents["manifest.json"] = fixture.canonical(self.manifest)
        contents.update(changes or {})
        target = self.base / "changed.tar"
        with tarfile.open(target, "w") as archive:
            for name, data in contents.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            if extra:
                member, data = extra
                archive.addfile(member, io.BytesIO(data) if data else None)
        return target

    def test_restore_works_after_source_is_removed_and_preserves_private_modes(self):
        shutil.rmtree(self.source)
        result = fixture.restore_bundle(self.bundle, self.expected, self.destination)
        self.assertEqual(result["rows_restored"], 2)
        self.assertTrue(result["logical_state_match"])
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o700)
        for path in self.destination.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_existing_restore_is_never_overwritten(self):
        self.destination.mkdir()
        marker = self.destination / "keep"
        marker.write_bytes(b"untouched")
        with self.assertRaises(fixture.FixtureError):
            fixture.restore_bundle(self.bundle, self.expected, self.destination)
        self.assertEqual(marker.read_bytes(), b"untouched")

    def test_manifest_hash_mismatch_writes_nothing(self):
        with self.assertRaisesRegex(fixture.FixtureError, "Manifest integrity"):
            fixture.restore_bundle(self.bundle, "0" * 64, self.destination)
        self.assertFalse(self.destination.exists())

    def test_changed_file_cannot_pass_the_captured_manifest(self):
        changed = self.rewritten({"application.json": b'{"synthetic_only":false}'})
        with self.assertRaisesRegex(fixture.FixtureError, "file integrity"):
            fixture.restore_bundle(changed, self.expected, self.destination)
        self.assertFalse(self.destination.exists())

    def test_duplicate_paths_traversal_links_and_devices_are_rejected(self):
        for name, kind in (("state.sqlite", tarfile.REGTYPE),
                           ("../escape", tarfile.REGTYPE),
                           ("/escape", tarfile.REGTYPE),
                           ("unexpected", tarfile.SYMTYPE),
                           ("unexpected", tarfile.LNKTYPE),
                           ("unexpected", tarfile.CHRTYPE)):
            with self.subTest(name=name, kind=kind):
                member = tarfile.TarInfo(name)
                member.type, member.linkname = kind, "state.sqlite"
                changed = self.rewritten(extra=(member, None))
                with self.assertRaises(fixture.FixtureError):
                    fixture.restore_bundle(changed, self.expected, self.destination)
                self.assertFalse(self.destination.exists())
                self.assertFalse((self.base / "escape").exists())

    def test_replacement_symlink_and_oversized_member_are_rejected(self):
        for kind, size in ((tarfile.SYMTYPE, 0), (tarfile.REGTYPE, fixture.MAX_FILE_BYTES + 1)):
            with self.subTest(kind=kind):
                changed = self.base / "invalid.tar"
                with tarfile.open(changed, "w") as archive:
                    for name in sorted(fixture.FILES | {"manifest.json"}):
                        member = tarfile.TarInfo(name)
                        member.type = kind if name == "state.sqlite" else tarfile.REGTYPE
                        member.linkname = "outside"
                        data = b"x" * (size if name == "state.sqlite" else 1)
                        member.size = len(data)
                        archive.addfile(member, io.BytesIO(data))
                with self.assertRaisesRegex(fixture.FixtureError, "type or size"):
                    fixture.restore_bundle(changed, self.expected, self.destination)
                self.assertFalse(self.destination.exists())

    def test_whole_artifact_bound_and_symlink_are_rejected(self):
        oversized = self.base / "too-large.tar"
        oversized.write_bytes(b"x" * (fixture.MAX_ARTIFACT_BYTES + 1))
        link = self.base / "link.tar"
        link.symlink_to(self.bundle)
        for path in (oversized, link):
            with self.subTest(path=path), self.assertRaises(fixture.FixtureError):
                fixture.restore_bundle(path, self.expected, self.destination)
            self.assertFalse(self.destination.exists())

    def test_invalid_database_is_removed_even_with_matching_file_hash(self):
        data = b"not SQLite"
        self.manifest["files"]["state.sqlite"] = {"sha256": fixture.digest(data), "size_bytes": len(data)}
        changed = self.rewritten({"state.sqlite": data})
        with self.assertRaisesRegex(fixture.FixtureError, "application verification"):
            fixture.restore_bundle(changed, fixture.digest(fixture.canonical(self.manifest)), self.destination)
        self.assertFalse(self.destination.exists())

    def test_cli_rejects_source_or_provider_arguments_before_any_operation(self):
        with mock.patch.object(fixture, "roundtrip") as operation, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(fixture.main(["--source", "/not-a-real-source"]), 2)
        operation.assert_not_called()

    def test_child_environment_does_not_inherit_credentials_and_errors_are_redacted(self):
        response = mock.Mock(returncode=1, stdout=b"untrusted-subprocess-output")
        with mock.patch.dict(os.environ, {"AWS_SECRET_ACCESS_KEY": "fixture-credential"}), \
                mock.patch.object(fixture.subprocess, "run", return_value=response) as command:
            with self.assertRaises(fixture.FixtureError) as caught:
                fixture.command(["gpg", "--version"])
        self.assertNotIn("fixture-credential", str(caught.exception))
        self.assertNotIn("untrusted-subprocess-output", str(caught.exception))
        self.assertEqual(command.call_args.kwargs["env"], {"PATH": "/usr/bin:/bin", "LANG": "C"})

    def test_cleanup_failure_is_observable_and_cannot_pass_the_cli(self):
        keyring = fixture.Keyring(self.base / "test-keyring")
        with mock.patch.object(fixture, "command", side_effect=fixture.FixtureError("synthetic failure")):
            self.assertFalse(keyring.close())
        for agent, files in ((False, True), (True, False), (False, False)):
            report = {"agent_cleanup_confirmed": agent, "temporary_files_removed": files}
            with self.subTest(agent=agent, files=files), \
                    mock.patch.object(fixture, "roundtrip", return_value=report), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(fixture.main([]), 1)
            self.assertEqual(json.loads(output.getvalue()), report)


class RealGPGTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not all(shutil.which(tool, path="/usr/bin:/bin") for tool in ("gpg", "gpgconf")):
            raise RuntimeError("Real GnuPG is required; cryptographic checks may not be skipped")
        cls.temporary = tempfile.TemporaryDirectory(prefix="rbx-fixture-gpg-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.base = Path(cls.temporary.name)
        cls.recovery = fixture.Keyring(cls.base / "recovery")
        cls.addClassCleanup(cls.recovery.close)
        cls.producer = fixture.Keyring(cls.base / "producer")
        cls.addClassCleanup(cls.producer.close)
        cls.recipient = cls.recovery.generate()
        public = cls.base / "public.asc"
        fixture.write_private(public, cls.recovery.run("--export", cls.recipient))
        cls.producer.run("--import", str(public))
        cls.plaintext = cls.base / "fixture.txt"
        fixture.write_private(cls.plaintext, b"synthetic test contents")
        cls.ciphertext = cls.base / "fixture.gpg"
        cls.producer.run("--trust-model", "always", "--no-encrypt-to", "--recipient", cls.recipient + "!",
                         "--output", str(cls.ciphertext), "--encrypt", str(cls.plaintext))
        cls.hash = fixture.digest(cls.ciphertext.read_bytes())

    def test_real_pipeline_reports_its_limits_and_cleans_all_temporary_material(self):
        parents = set(Path(tempfile.gettempdir()).glob("rbx-fixture-*"))
        report = fixture.roundtrip()
        self.assertTrue(report["synthetic_only"])
        self.assertTrue(report["ciphertext_readback_match"])
        self.assertFalse(report["provider_integration_tested"])
        self.assertFalse(report["headscale_runtime_tested"])
        self.assertFalse(report["retention_enforcement_tested"])
        self.assertFalse(report["producer_private_keys"])
        self.assertTrue(report["agent_cleanup_confirmed"])
        self.assertTrue(report["temporary_files_removed"])
        self.assertEqual(report["rows_restored"], 2)
        self.assertEqual(report["transport"], "local-copy")
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("rbx-fixture-*")), parents)

    def test_real_decryption_matches_plaintext_and_fails_with_missing_private_key(self):
        destination = self.base / "readback.txt"
        fixture.decrypt(self.ciphertext, self.hash, self.recovery, destination)
        self.assertEqual(destination.read_bytes(), self.plaintext.read_bytes())
        destination.unlink()
        with self.assertRaises(fixture.FixtureError):
            fixture.decrypt(self.ciphertext, self.hash, self.producer, destination)
        self.assertFalse(destination.exists())

    def test_ciphertext_tamper_is_rejected_before_decryption(self):
        changed = self.base / "corrupt.gpg"
        data = self.ciphertext.read_bytes()
        changed.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
        destination = self.base / "tamper.txt"
        with mock.patch.object(self.recovery, "run") as decrypt:
            with self.assertRaisesRegex(fixture.FixtureError, "Ciphertext integrity"):
                fixture.decrypt(changed, self.hash, self.recovery, destination)
        decrypt.assert_not_called()
        # Even an updated outer hash cannot make corrupt authenticated ciphertext valid.
        with self.assertRaises(fixture.FixtureError):
            fixture.decrypt(changed, fixture.digest(changed.read_bytes()), self.recovery, destination)
        self.assertFalse(destination.exists())

    def test_decompression_is_bounded_and_partial_plaintext_is_removed(self):
        large = self.base / "large.txt"
        fixture.write_private(large, b"x" * (fixture.MAX_ARTIFACT_BYTES + 1))
        encrypted = self.base / "compressed.gpg"
        self.producer.run("--trust-model", "always", "--compress-algo", "ZIP",
                          "--recipient", self.recipient + "!", "--output", str(encrypted),
                          "--encrypt", str(large))
        self.assertLess(encrypted.stat().st_size, fixture.MAX_ARTIFACT_BYTES)
        destination = self.base / "too-large.txt"
        with self.assertRaises(fixture.FixtureError):
            fixture.decrypt(encrypted, fixture.digest(encrypted.read_bytes()), self.recovery, destination)
        self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
