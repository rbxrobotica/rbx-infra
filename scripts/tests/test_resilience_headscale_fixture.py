"""Adversarial synthetic-only capture/restore tests; no GPG or live inputs."""

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from resilience import headscale_fixture as fixture


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def contents(bundle):
    with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:") as archive:
        return {member.name: archive.extractfile(member).read() for member in archive.getmembers()}


def rewritten(bundle, *, changes=None, dropped=(), extras=()):
    payload = contents(bundle)
    payload.update(changes or {})
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in payload.items():
            if name in dropped:
                continue
            member = tarfile.TarInfo(name)
            member.mode = 0o600
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        for member, data in extras:
            archive.addfile(member, io.BytesIO(data) if data is not None else None)
    return result.getvalue()


class HeadscaleCaptureFixtureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rbx-headscale-tests-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.source = self.enterContext(fixture.make_source(self.base / "source"))
        self.destination = self.base / "restored"

    def assert_rejected(self, bundle, expected):
        with self.assertRaises(fixture.CaptureError) as caught:
            fixture.restore(bundle, expected, self.destination)
        self.assertNotIn(str(self.base), str(caught.exception))
        self.assertFalse(self.destination.exists())
        self.assertFalse(self.destination.is_symlink())
        return caught.exception

    def forged(self, bundle, change_manifest=None, change_payload=None):
        payload = contents(bundle)
        manifest = json.loads(payload["manifest.json"])
        if change_payload:
            change_payload(payload)
            for name, data in payload.items():
                if name != "manifest.json" and name in manifest["files"]:
                    manifest["files"][name] = {"size_bytes": len(data), "sha256": digest(data),
                                               "mode": "0600"}
        if change_manifest:
            change_manifest(manifest)
        encoded = canonical(manifest)
        payload["manifest.json"] = encoded
        return rewritten(bundle, changes=payload), digest(encoded)

    def test_committed_wal_is_captured_but_pending_writer_transaction_is_excluded(self):
        writer = self.source.writer
        self.assertEqual(writer.execute("PRAGMA journal_mode").fetchone(), ("wal",))
        self.assertGreater((self.source.directory / (fixture.DATABASE + "-wal")).stat().st_size, 0)
        main = self.source.directory / fixture.DATABASE
        with contextlib.closing(sqlite3.connect(main.as_uri() + "?mode=ro&immutable=1", uri=True)) as raw_main:
            self.assertNotIn(("nodes",), raw_main.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        main_before = main.read_bytes()
        wal_before = (self.source.directory / (fixture.DATABASE + "-wal")).read_bytes()
        writer.execute("INSERT INTO nodes VALUES (3,'synthetic-pending',1)")
        self.assertEqual(writer.execute("SELECT count(*) FROM nodes").fetchone(), (3,))
        bundle, expected = fixture.capture(self.source)
        self.assertTrue(writer.in_transaction)
        self.assertEqual(main.read_bytes(), main_before)
        self.assertEqual((self.source.directory / (fixture.DATABASE + "-wal")).read_bytes(), wal_before)
        writer.close()
        shutil.rmtree(self.source.directory)
        report = fixture.restore(bundle, expected, self.destination)
        self.assertEqual(report["nodes_restored"], 2)
        self.assertTrue(report["snapshot_model_verified"])
        with contextlib.closing(sqlite3.connect((self.destination / fixture.SNAPSHOT).as_uri()
                                                + "?mode=ro&immutable=1", uri=True)) as restored:
            self.assertEqual(restored.execute("SELECT id,name,user_id FROM nodes ORDER BY id").fetchall(),
                             fixture.NODES)
        self.assertFalse((self.destination / (fixture.SNAPSHOT + "-wal")).exists())
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o700)
        for path in self.destination.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_restore_checks_captured_state_even_after_source_changes(self):
        bundle, expected = fixture.capture(self.source)
        self.source.writer.execute("INSERT INTO nodes VALUES (3,'synthetic-later',1)")
        self.source.writer.commit()
        self.assertEqual(self.source.writer.execute("SELECT count(*) FROM nodes").fetchone(), (3,))
        report = fixture.restore(bundle, expected, self.destination)
        self.assertEqual(report["nodes_restored"], 2)

    def test_both_profiles_restore_and_embedded_derp_identity_is_required(self):
        bundle, expected = fixture.capture(self.source)
        self.assertNotIn("derp.key", contents(bundle))
        report = fixture.restore(bundle, expected, self.destination)
        self.assertFalse(report["embedded_derp"])
        shutil.rmtree(self.destination)
        with fixture.make_source(self.base / "with-derp", embedded_derp=True) as source:
            bundle, expected = fixture.capture(source)
            self.assertIn("derp.key", contents(bundle))
            report = fixture.restore(bundle, expected, self.destination)
            self.assertTrue(report["embedded_derp"])
            shutil.rmtree(self.destination)
            self.assert_rejected(rewritten(bundle, dropped=("derp.key",)), expected)
            (source.directory / "derp.key").unlink()
            with self.assertRaisesRegex(fixture.CaptureError, "^missing_source_file$"):
                fixture.capture(source)

    def test_missing_identity_tls_policy_config_or_database_stops_capture(self):
        for index, name in enumerate(sorted(fixture.BASE_FILES | {fixture.DATABASE})):
            with self.subTest(name=name), fixture.make_source(self.base / f"missing-{index}") as source:
                (source.directory / name).unlink()
                with self.assertRaisesRegex(fixture.CaptureError, "^missing_source_file$"):
                    fixture.capture(source)

    def test_unknown_source_entries_and_unapproved_derp_fail_without_disclosing_names(self):
        for index, name in enumerate(("unexpected-private-name", "derp.key", "nested")):
            with self.subTest(name=name), fixture.make_source(self.base / f"unknown-{index}") as source:
                unexpected = source.directory / name
                if name == "nested":
                    unexpected.mkdir()
                else:
                    unexpected.write_bytes(b"synthetic-data")
                    unexpected.chmod(0o600)
                with self.assertRaises(fixture.CaptureError) as caught:
                    fixture.capture(source)
                self.assertEqual(str(caught.exception), "unexpected_source_entry")
                self.assertNotIn(name, str(caught.exception))

    def test_symlink_fifo_hardlink_and_unsafe_source_modes_are_rejected(self):
        for index, kind in enumerate(("symlink", "fifo", "hardlink", "permissions")):
            with self.subTest(kind=kind), fixture.make_source(self.base / f"unsafe-{index}") as source:
                path = source.directory / "noise.key"
                if kind == "permissions":
                    path.chmod(0o644)
                else:
                    path.unlink()
                    if kind == "fifo":
                        os.mkfifo(path, 0o600)
                    else:
                        outside = self.base / f"outside-{index}"
                        outside.write_bytes(fixture.IDENTITY)
                        outside.chmod(0o600)
                        if kind == "symlink":
                            path.symlink_to(outside)
                        else:
                            os.link(outside, path)
                with self.assertRaisesRegex(fixture.CaptureError, "^unsafe_source_file$"):
                    fixture.capture(source)
        self.source.directory.chmod(0o755)
        with self.assertRaisesRegex(fixture.CaptureError, "^unsafe_source_directory$"):
            fixture.capture(self.source)

    def test_changed_bytes_or_same_bytes_with_changed_metadata_during_snapshot_are_rejected(self):
        original = fixture._snapshot
        for index, same_bytes in enumerate((False, True)):
            with self.subTest(same_bytes=same_bytes), \
                    fixture.make_source(self.base / f"changed-{index}") as source:
                path = source.directory / "noise.key"
                def changed(active, deadline):
                    result = original(active, deadline)
                    info = path.stat()
                    path.write_bytes(fixture.IDENTITY if same_bytes else b"synthetic-replacement")
                    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1))
                    return result
                with mock.patch.object(fixture, "_snapshot", side_effect=changed), \
                        self.assertRaisesRegex(fixture.CaptureError, "^source_changed$"):
                    fixture.capture(source)
                self.assertEqual(set(p.name for p in source.directory.iterdir()),
                                 fixture.BASE_FILES | {fixture.DATABASE, fixture.DATABASE + "-wal",
                                                       fixture.DATABASE + "-shm"})

    def test_changed_baseline_is_rejected_before_database_snapshot(self):
        (self.source.directory / "noise.key").write_bytes(b"synthetic-replacement")
        with mock.patch.object(fixture, "_snapshot") as snapshot, \
                self.assertRaisesRegex(fixture.CaptureError, "^source_baseline_mismatch$"):
            fixture.capture(self.source)
        snapshot.assert_not_called()

    def test_capture_file_database_total_bundle_and_deadline_bounds(self):
        cases = (("MAX_FILE_BYTES", 1, "unsafe_source_file"),
                 ("MAX_FILE_BYTES", 8192, "sqlite_size"),
                 ("MAX_TOTAL_BYTES", 1, "capture_size"),
                 ("MAX_BUNDLE_BYTES", 1, "bundle_size"))
        for constant, bound, code in cases:
            with self.subTest(constant=constant, bound=bound), \
                    mock.patch.object(fixture, constant, bound), \
                    self.assertRaisesRegex(fixture.CaptureError, f"^{code}$"):
                fixture.capture(self.source)
        with mock.patch.object(fixture.time, "monotonic", side_effect=(0, fixture.CAPTURE_SECONDS + 1)), \
                self.assertRaisesRegex(fixture.CaptureError, "^capture_deadline$"):
            fixture.capture(self.source)

    def test_oversized_archive_or_member_and_nonprivate_member_mode_are_rejected(self):
        bundle, expected = fixture.capture(self.source)
        with mock.patch.object(fixture, "MAX_BUNDLE_BYTES", len(bundle) - 1):
            self.assert_rejected(bundle, expected)
        self.assert_rejected(rewritten(bundle, changes={"noise.key": b"x" * (fixture.MAX_FILE_BYTES + 1)}),
                             expected)
        member = tarfile.TarInfo("noise.key")
        member.size, member.mode = len(fixture.IDENTITY), 0o644
        changed = rewritten(bundle, dropped=("noise.key",), extras=((member, fixture.IDENTITY),))
        self.assert_rejected(changed, expected)

    def test_pax_path_override_cannot_escape_restore(self):
        bundle, expected = fixture.capture(self.source)
        result = io.BytesIO()
        with tarfile.open(fileobj=result, mode="w", format=tarfile.PAX_FORMAT) as archive:
            member = tarfile.TarInfo("noise.key")
            member.pax_headers = {"path": "../escape"}
            member.size, member.mode = 1, 0o600
            archive.addfile(member, io.BytesIO(b"x"))
        self.assertEqual(str(self.assert_rejected(result.getvalue(), expected)), "invalid_member")
        self.assertFalse((self.base / "escape").exists())

    def test_approved_member_replacement_links_fifo_devices_or_directory_are_rejected(self):
        bundle, expected = fixture.capture(self.source)
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE,
                     tarfile.CHRTYPE, tarfile.DIRTYPE):
            with self.subTest(kind=kind):
                member = tarfile.TarInfo("noise.key")
                member.type, member.mode, member.linkname = kind, 0o600, "../outside"
                member.size = 1  # Keep the independent size/mode/name gates valid.
                changed = rewritten(bundle, dropped=("noise.key",), extras=((member, None),))
                self.assertEqual(str(self.assert_rejected(changed, expected)), "invalid_member")

    def test_changed_payload_is_rejected_by_original_manifest(self):
        bundle, expected = fixture.capture(self.source)
        self.assert_rejected(rewritten(bundle, changes={"noise.key": b"synthetic-replacement"}), expected)

    def test_forged_manifest_schema_profiles_types_scope_and_interval_are_rejected(self):
        bundle, _ = fixture.capture(self.source)
        changes = (
            lambda m: m.update(schema="unexpected"),
            lambda m: m.update(synthetic_only=1),
            lambda m: m.update(headscale_native_schema=0),
            lambda m: m.update(embedded_derp=0),
            lambda m: m.update(files=None),
            lambda m: m["files"].pop("noise.key"),
            lambda m: m["files"]["noise.key"].update(size_bytes=float(len(fixture.IDENTITY))),
            lambda m: m["model"].update(users=True),
            lambda m: m.update(model={}),
            lambda m: m.update(unrecognized=True),
            lambda m: m.update(started_utc="not-a-time"),
            lambda m: m.update(started_utc="2030-01-01T00:00:00+00:00",
                               finished_utc="2020-01-01T00:00:00+00:00"),
            lambda m: m.update(started_utc="2020-01-01T00:00:00",
                               finished_utc="2020-01-01T00:00:01"),
            lambda m: m.update(started_utc="2020-01-01T00:00:00+01:00",
                               finished_utc="2020-01-01T00:00:01+01:00"),
        )
        for index, change in enumerate(changes):
            with self.subTest(case=index):
                changed, expected = self.forged(bundle, change_manifest=change)
                self.assert_rejected(changed, expected)

    def test_duplicate_manifest_json_keys_and_nonfinite_numbers_are_rejected(self):
        bundle, _ = fixture.capture(self.source)
        original = contents(bundle)["manifest.json"]
        for encoded in (b'{"schema":"forged",' + original[1:],
                        original.replace(b'"synthetic_only":true', b'"synthetic_only":NaN')):
            with self.subTest(prefix=encoded[:20]):
                changed = rewritten(bundle, changes={"manifest.json": encoded})
                self.assert_rejected(changed, digest(encoded))

    def test_rehashed_lost_identity_or_configuration_inconsistency_is_rejected(self):
        bundle, _ = fixture.capture(self.source)
        replacements = {"noise.key": b"synthetic-other-identity", "policy.hujson": b'{"grants":[1]}',
                        "config.yaml": b'{"synthetic_only":false}',
                        "tls-account.json": b'{"account":"synthetic-replacement"}'}
        for name, value in replacements.items():
            with self.subTest(name=name):
                changed, expected = self.forged(bundle, change_payload=lambda payload: payload.update({name: value}))
                self.assert_rejected(changed, expected)

    def test_invalid_database_or_rehashed_model_cleans_partial_restore(self):
        bundle, _ = fixture.capture(self.source)
        invalid, expected = self.forged(bundle, change_payload=lambda payload: payload.update({fixture.SNAPSHOT: b"not SQLite"}))
        self.assert_rejected(invalid, expected)
        changed, expected = self.forged(bundle, change_manifest=lambda m: m["model"].update(nodes_sha256="0" * 64))
        self.assert_rejected(changed, expected)

    def test_reports_and_untrusted_exceptions_are_redacted(self):
        bundle, expected = fixture.capture(self.source)
        report = fixture.restore(bundle, expected, self.destination)
        encoded = json.dumps(report)
        for value in (str(self.base), fixture.IDENTITY.decode().strip(),
                      "synthetic-workstation", "synthetic-owner", expected):
            self.assertNotIn(value, encoded)
        with mock.patch.object(fixture, "_snapshot", side_effect=RuntimeError("synthetic-private-path")), \
                self.assertRaisesRegex(fixture.CaptureError, "^capture_failed$"):
            fixture.capture(self.source)

    def test_source_context_closes_writer_on_success_and_exception(self):
        for index, fail in enumerate((False, True)):
            try:
                with fixture.make_source(self.base / f"context-{index}") as source:
                    if fail:
                        raise RuntimeError("synthetic-context-error")
            except RuntimeError:
                self.assertTrue(fail)
            with self.assertRaises(sqlite3.ProgrammingError):
                source.writer.execute("SELECT 1")

    def test_roundtrip_failure_closes_only_fixture_keyrings_and_removes_temporary_tree(self):
        before = set(Path(tempfile.gettempdir()).glob("rbx-headscale-shaped-*"))
        producer, recovery = mock.Mock(), mock.Mock()
        recovery.generate.side_effect = RuntimeError("synthetic-private-failure")
        with mock.patch.object(fixture.crypto, "Keyring", side_effect=(producer, recovery)), \
                self.assertRaises(RuntimeError):
            fixture.roundtrip()
        producer.close.assert_called_once_with()
        recovery.close.assert_called_once_with()
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("rbx-headscale-shaped-*")), before)

    def test_cli_failure_redaction_and_cleanup_flags_cannot_pass(self):
        with mock.patch.object(fixture, "roundtrip", side_effect=RuntimeError("synthetic-private-failure")), \
                contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(fixture.main([]), 1)
        self.assertNotIn("synthetic-private-failure", error.getvalue())
        for agents, files in ((False, True), (True, False), (False, False)):
            report = {"agent_cleanup_confirmed": agents, "temporary_files_removed": files}
            with self.subTest(agents=agents, files=files), \
                    mock.patch.object(fixture, "roundtrip", return_value=report), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(fixture.main([]), 1)

    def test_existing_destination_is_never_overwritten(self):
        bundle, expected = fixture.capture(self.source)
        self.destination.mkdir()
        marker = self.destination / "keep"
        marker.write_bytes(b"synthetic-existing-data")
        with self.assertRaises(fixture.CaptureError):
            fixture.restore(bundle, expected, self.destination)
        self.assertEqual(marker.read_bytes(), b"synthetic-existing-data")

    def test_existing_file_and_dangling_destination_symlink_are_preserved(self):
        bundle, expected = fixture.capture(self.source)
        self.destination.write_bytes(b"synthetic-existing-file")
        with self.assertRaises(fixture.CaptureError):
            fixture.restore(bundle, expected, self.destination)
        self.assertEqual(self.destination.read_bytes(), b"synthetic-existing-file")
        self.destination.unlink()
        self.destination.symlink_to(self.base / "missing-target")
        with self.assertRaises(fixture.CaptureError):
            fixture.restore(bundle, expected, self.destination)
        self.assertTrue(self.destination.is_symlink())
        self.assertFalse((self.base / "missing-target").exists())

    def test_manifest_hash_mismatch_writes_nothing(self):
        bundle, _ = fixture.capture(self.source)
        self.assert_rejected(bundle, "0" * 64)

    def test_empty_malformed_and_truncated_bundles_write_nothing(self):
        bundle, expected = fixture.capture(self.source)
        for invalid in (b"", b"synthetic-not-a-tar", bundle[:100]):
            with self.subTest(size=len(invalid)):
                self.assert_rejected(invalid, expected)

    def test_unknown_duplicate_traversal_links_devices_and_directories_are_rejected(self):
        bundle, expected = fixture.capture(self.source)
        existing = next(iter(contents(bundle)))
        cases = (("unexpected", tarfile.REGTYPE), (existing, tarfile.REGTYPE),
                 ("../escape", tarfile.REGTYPE), ("/escape", tarfile.REGTYPE),
                 ("nested/../escape", tarfile.REGTYPE), ("unexpected", tarfile.SYMTYPE),
                 ("unexpected", tarfile.LNKTYPE), ("unexpected", tarfile.FIFOTYPE),
                 ("unexpected", tarfile.CHRTYPE), ("unexpected", tarfile.DIRTYPE))
        for name, kind in cases:
            with self.subTest(name=name, kind=kind):
                member = tarfile.TarInfo(name)
                member.type = kind
                member.linkname = "../outside"
                data = b"x" if kind == tarfile.REGTYPE else None
                member.size = len(data) if data is not None else 0
                changed = rewritten(bundle, extras=((member, data),))
                self.assert_rejected(changed, expected)
                self.assertFalse((self.base / "escape").exists())

    def test_cli_refuses_source_provider_or_any_other_argument_before_operation(self):
        for arguments in (["--source", "/synthetic-private-input"], ["--provider", "fixture"],
                          ["--help"], ["unexpected-positional"]):
            with self.subTest(arguments=arguments), \
                    mock.patch.object(fixture, "roundtrip") as operation, \
                    contextlib.redirect_stdout(io.StringIO()) as output, \
                    contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(fixture.main(arguments), 2)
            operation.assert_not_called()
            self.assertNotIn("/synthetic-private-input", output.getvalue() + error.getvalue())


if __name__ == "__main__":
    unittest.main()
