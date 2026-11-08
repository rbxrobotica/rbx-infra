"""Package/control-flow tests with synthetic bytes and mocked GPG commands.

Real cryptographic protection and recovery are exercised only by the primary
agent's separately isolated compatibility fixture, never by these mocks.
"""

import contextlib
import copy
import hashlib
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from resilience import gpg_emergency_fixture as fixture


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
            member.mode, member.size = 0o600, len(data)
            archive.addfile(member, io.BytesIO(data))
        for member, data in extras:
            archive.addfile(member, io.BytesIO(data) if data is not None else None)
    return result.getvalue()


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.files = {name: b"synthetic-" + name.encode() for name in fixture.FILES - {"recovery.json"}}
        self.files["public-key.gpg"] = b"x"  # Exposes bool-for-int size equality.
        self.files["instructions.txt"] = fixture.INSTRUCTIONS
        self.recovery = {"schema": fixture.SCHEMA, "synthetic_only": True,
                         "inner_passphrase": "a" * 64, "fingerprints": ["A" * 40, "B" * 40],
                         "canary_sha256": digest(fixture.CANARY)}
        self.bundle, self.expected = fixture.make_package(self.files, self.recovery)
        self.no_process = mock.patch.object(fixture.subprocess, "Popen",
                                             side_effect=AssertionError("no real subprocess allowed"))
        self.no_process.start()
        self.addCleanup(self.no_process.stop)

    def rejected(self, bundle, expected=None, code=None):
        with self.assertRaises(fixture.KitError) as caught:
            fixture.unpack(bundle, self.expected if expected is None else expected)
        self.assertRegex(str(caught.exception), r"^[a-z][a-z0-9_]*$")
        if code:
            self.assertEqual(str(caught.exception), code)
        return caught.exception

    def forged(self, *, manifest_change=None, recovery_change=None, payload_changes=None):
        payload = contents(self.bundle)
        manifest = json.loads(payload["manifest.json"])
        if recovery_change:
            recovery = json.loads(payload["recovery.json"])
            recovery_change(recovery)
            payload["recovery.json"] = canonical(recovery)
        payload.update(payload_changes or {})
        for name, data in payload.items():
            if name != "manifest.json" and name in manifest["files"]:
                manifest["files"][name] = {"size_bytes": len(data), "sha256": digest(data)}
        if manifest_change:
            manifest_change(manifest)
        encoded = canonical(manifest)
        payload["manifest.json"] = encoded
        return rewritten(self.bundle, changes=payload), digest(encoded)

    def test_valid_package_returns_exact_fixed_members_and_inner_recovery_metadata(self):
        files, recovery = fixture.unpack(self.bundle, self.expected)
        self.assertEqual(set(files), fixture.FILES)
        self.assertEqual(recovery, self.recovery)
        self.assertEqual({name: data for name, data in files.items() if name != "recovery.json"}, self.files)
        self.assertEqual(json.loads(files["recovery.json"]), self.recovery)
        self.assertNotIn("outer_secret", files["recovery.json"].decode())

    def test_make_package_rejects_unknown_missing_or_nonmapping_scope(self):
        for files in ({**self.files, "unexpected": b"x"},
                      {name: data for name, data in self.files.items() if name != "private-key.gpg"},
                      {**self.files, "recovery.json": b"injected"}, [], None):
            with self.subTest(kind=type(files).__name__), \
                    self.assertRaisesRegex(fixture.KitError, "^invalid_package_scope$"):
                fixture.make_package(files, self.recovery)

    def test_make_package_enforces_member_types_sizes_and_total_archive_bound(self):
        for value in (b"", "synthetic-string", bytearray(b"x"), b"x" * (fixture.MAX_MEMBER_BYTES + 1)):
            with self.subTest(kind=type(value).__name__), \
                    self.assertRaisesRegex(fixture.KitError, "^package_member_bound$"):
                fixture.make_package({**self.files, "private-key.gpg": value}, self.recovery)
        with mock.patch.object(fixture, "MAX_KIT_BYTES", len(self.bundle) - 1), \
                self.assertRaisesRegex(fixture.KitError, "^package_bound$"):
            fixture.make_package(self.files, self.recovery)

    def test_recovery_metadata_exact_schema_types_fingerprints_and_canary_are_required(self):
        changes = (lambda r: r.update(schema="wrong"), lambda r: r.update(synthetic_only=1),
                   lambda r: r.update(inner_passphrase="a" * 63),
                   lambda r: r.update(inner_passphrase="A" * 64),
                   lambda r: r.update(inner_passphrase="a" * 63 + "\n"),
                   lambda r: r.update(fingerprints=["A" * 40, "A" * 40]),
                   lambda r: r.update(fingerprints=["A" * 40]),
                   lambda r: r.update(fingerprints=["A" * 40, "b" * 40]),
                   lambda r: r.update(fingerprints=("A" * 40, "B" * 40)),
                   lambda r: r.update(canary_sha256="0" * 64),
                   lambda r: r.update(outer_secret="must-not-be-present"),
                   lambda r: r.pop("inner_passphrase"))
        for index, change in enumerate(changes):
            with self.subTest(case=index):
                recovery = copy.deepcopy(self.recovery)
                change(recovery)
                with self.assertRaisesRegex(fixture.KitError, "^invalid_recovery_metadata$"):
                    fixture.make_package(self.files, recovery)
                # JSON turns tuples into lists, so test that API-only type separately.
                if not isinstance(recovery.get("fingerprints"), tuple):
                    bundle, expected = self.forged(recovery_change=change)
                    self.rejected(bundle, expected, "invalid_recovery_metadata")

    def test_manifest_hash_payload_hash_and_missing_members_fail_independently(self):
        self.rejected(self.bundle, "0" * 64, "manifest_integrity")
        self.rejected(rewritten(self.bundle, changes={"private-key.gpg": b"synthetic-tamper"}),
                      code="payload_integrity")
        for name in fixture.FILES | {"manifest.json"}:
            with self.subTest(name=name):
                self.rejected(rewritten(self.bundle, dropped=(name,)), code="missing_package_member")

    def test_rehashed_manifest_exact_types_scope_and_metadata_are_still_enforced(self):
        changes = (lambda m: m.update(schema="wrong"), lambda m: m.update(synthetic_only=1),
                   lambda m: m.update(files=[]), lambda m: m.update(unexpected=True),
                   lambda m: m["files"].pop("private-key.gpg"),
                   lambda m: m["files"]["public-key.gpg"].update(size_bytes=True),
                   lambda m: m["files"]["private-key.gpg"].update(size_bytes=float(len(self.files["private-key.gpg"]))),
                   lambda m: m["files"]["private-key.gpg"].update(sha256="0" * 64),
                   lambda m: m["files"]["private-key.gpg"].update(unexpected=True),
                   lambda m: m["files"].update({"private-key.gpg": None}))
        for index, change in enumerate(changes):
            with self.subTest(case=index):
                bundle, expected = self.forged(manifest_change=change)
                self.rejected(bundle, expected)

    def test_duplicate_json_keys_and_nonfinite_numbers_fail_even_with_rehashed_manifest(self):
        original = contents(self.bundle)["manifest.json"]
        variants = (b'{"schema":"wrong",' + original[1:],
                    original.replace(b'"synthetic_only":true', b'"synthetic_only":NaN'))
        for encoded in variants:
            self.rejected(rewritten(self.bundle, changes={"manifest.json": encoded}), digest(encoded))
        recovery = contents(self.bundle)["recovery.json"]
        malformed = b'{"inner_passphrase":"wrong",' + recovery[1:]
        bundle, expected = self.forged(payload_changes={"recovery.json": malformed})
        self.rejected(bundle, expected, "duplicate_json_key")

    def test_rehashed_instructions_cannot_change_fixture_semantics(self):
        bundle, expected = self.forged(payload_changes={"instructions.txt": b"synthetic altered instructions"})
        self.rejected(bundle, expected, "invalid_fixture_instructions")

    def test_invalid_bundle_hash_type_truncation_and_whole_size_are_rejected(self):
        for value in (None, bytearray(self.bundle), b"", b"not a tar", self.bundle[:100]):
            with self.subTest(kind=type(value).__name__):
                self.rejected(value)
        for expected in (None, "a" * 63, "A" * 64, "g" * 64):
            with self.subTest(expected_type=type(expected).__name__):
                # None needs a direct call because rejected(None) means default hash.
                with self.assertRaisesRegex(fixture.KitError, "^invalid_package$"):
                    fixture.unpack(self.bundle, expected)
        with mock.patch.object(fixture, "MAX_KIT_BYTES", len(self.bundle) - 1):
            self.rejected(self.bundle, code="invalid_package")

    def test_duplicate_unknown_absolute_and_traversal_tar_names_are_rejected(self):
        for name in ("private-key.gpg", "unexpected", "../escape", "/escape", "nested/../escape"):
            with self.subTest(name=name):
                member = tarfile.TarInfo(name)
                member.mode, member.size = 0o600, 1
                # Keep count below the cap so the intended name/duplicate gate is reached.
                dropped = ("public-key.gpg",) if name == "private-key.gpg" else ("private-key.gpg",)
                self.rejected(rewritten(self.bundle, dropped=dropped, extras=((member, b"x"),)),
                              code="invalid_member")

    def test_approved_member_link_fifo_device_directory_and_nonprivate_modes_are_rejected(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE,
                     tarfile.BLKTYPE, tarfile.DIRTYPE):
            with self.subTest(kind=kind):
                member = tarfile.TarInfo("private-key.gpg")
                member.type, member.linkname = kind, "../outside"
                member.mode, member.size = 0o600, 1  # Other independent gates remain valid.
                changed = rewritten(self.bundle, dropped=(member.name,), extras=((member, None),))
                self.rejected(changed, code="invalid_member")
        member = tarfile.TarInfo("private-key.gpg")
        member.mode, member.size = 0o644, 1
        self.rejected(rewritten(self.bundle, dropped=(member.name,), extras=((member, b"x"),)),
                      code="invalid_member")

    def test_member_size_and_pax_path_override_are_rejected_at_member_gate(self):
        self.rejected(rewritten(self.bundle, changes={"private-key.gpg": b"x" * (fixture.MAX_MEMBER_BYTES + 1)}),
                      code="invalid_member")
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
            member = tarfile.TarInfo("private-key.gpg")
            member.pax_headers = {"path": "../escape"}
            member.mode, member.size = 0o600, 1
            archive.addfile(member, io.BytesIO(b"x"))
        self.rejected(output.getvalue(), code="invalid_member")


class CommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rbx-gpg-emergency-tests-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.ring = fixture.Keyring(self.base / "ring")
        self.secret = b"a" * 64
        self.diagnostics = b"\n".join((b"[GNUPG:] DECRYPTION_OKAY", b"[GNUPG:] GOODMDC",
                                        b"[GNUPG:] END_DECRYPTION", b"[GNUPG:] DECRYPTION_INFO 2 9 0"))

    def fake_process(self, stdout=b"", stderr=b"", *, accept_input=False, code=0):
        def stream(value):
            reader, writer = os.pipe()
            os.write(writer, value)
            os.close(writer)
            result = os.fdopen(reader, "rb", buffering=0)
            self.addCleanup(result.close)
            return result
        stdin = None
        self.input_reader = None
        if accept_input:
            reader, writer = os.pipe()
            self.input_reader = os.fdopen(reader, "rb", buffering=0)
            self.addCleanup(self.input_reader.close)
            stdin = os.fdopen(writer, "wb", buffering=0)
            self.addCleanup(stdin.close)
        return mock.Mock(stdout=stream(stdout), stderr=stream(stderr), stdin=stdin,
                         pid=123456789, wait=mock.Mock(return_value=code), poll=mock.Mock(return_value=code))

    def test_runner_uses_only_explicit_argv_private_pipes_and_minimal_environment(self):
        process = self.fake_process(b"synthetic-output", b"synthetic-diagnostics", accept_input=True)
        ambient = {"AWS_SECRET_ACCESS_KEY": "ambient-secret", "GNUPGHOME": "/ambient/keyring",
                   "GPG_AGENT_INFO": "ambient-agent", "SSH_AUTH_SOCK": "/ambient/socket",
                   "HOME": "/ambient/home"}
        with mock.patch.dict(os.environ, ambient), \
                mock.patch.object(fixture.subprocess, "Popen", return_value=process) as popen:
            result = fixture.run_command(["/synthetic/command", "fixed"], input_bytes=b"synthetic-stdin")
        self.assertEqual(result, (0, b"synthetic-output", b"synthetic-diagnostics"))
        self.assertEqual(self.input_reader.read(), b"synthetic-stdin")
        self.assertEqual(popen.call_args.args[0], ["/synthetic/command", "fixed"])
        self.assertFalse(popen.call_args.kwargs["shell"])
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(popen.call_args.kwargs["env"], {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})
        self.assertNotIn("ambient-secret", str(popen.call_args))

    def test_startup_failure_has_only_a_fixed_error_code(self):
        with mock.patch.object(fixture.subprocess, "Popen", side_effect=OSError("synthetic-private-path")), \
                self.assertRaisesRegex(fixture.KitError, "^command_unavailable$"):
            fixture.run_command(["/synthetic/command"])

    def test_combined_stdout_stderr_limit_kills_and_reaps_even_when_leader_exited(self):
        for stdout, stderr in ((b"secret-output", b""), (b"", b"secret-errors"), (b"123456", b"abcdef")):
            with self.subTest(stdout=len(stdout), stderr=len(stderr)):
                process = self.fake_process(stdout, stderr)
                with mock.patch.object(fixture.subprocess, "Popen", return_value=process), \
                        mock.patch.object(fixture.os, "killpg") as kill, \
                        self.assertRaisesRegex(fixture.KitError, "^command_output_bound$"):
                    fixture.run_command(["/synthetic/command"], limit=8)
                kill.assert_called_once_with(process.pid, fixture.signal.SIGKILL)
                process.wait.assert_called_once()

    def test_deadline_and_wait_timeout_kill_and_reap_the_process_group(self):
        process = self.fake_process()
        with mock.patch.object(fixture.subprocess, "Popen", return_value=process), \
                mock.patch.object(fixture.time, "monotonic", side_effect=(0, 11)), \
                mock.patch.object(fixture.os, "killpg") as kill, \
                self.assertRaisesRegex(fixture.KitError, "^command_timeout$"):
            fixture.run_command(["/synthetic/command"], timeout=10)
        kill.assert_called_once_with(process.pid, fixture.signal.SIGKILL)
        process = self.fake_process()
        process.wait.side_effect = (subprocess.TimeoutExpired("synthetic-private-command", 1), 0)
        with mock.patch.object(fixture.subprocess, "Popen", return_value=process), \
                mock.patch.object(fixture.os, "killpg") as kill, \
                self.assertRaisesRegex(fixture.KitError, "^command_timeout$"):
            fixture.run_command(["/synthetic/command"])
        self.assertEqual(process.wait.call_count, 2)
        kill.assert_called_once()

    def test_keyring_passphrase_is_only_stdin_and_data_only_import_is_separate(self):
        with mock.patch.object(fixture, "run_command", return_value=(0, b"synthetic-export", b"")) as command:
            self.assertEqual(self.ring.call("--export-secret-keys", passphrase=self.secret), b"synthetic-export")
        argv = command.call_args.args[0]
        self.assertNotIn(self.secret.decode(), str(argv))
        self.assertEqual(command.call_args.kwargs["input_bytes"], self.secret + b"\n")
        for flag in ("--no-options", "--batch", "--no-tty", "--no-symkey-cache", "--no-auto-key-retrieve"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--homedir") + 1], str(self.ring.directory))
        self.assertEqual(argv[argv.index("--passphrase-fd") + 1], "0")
        self.assertEqual(argv[argv.index("--pinentry-mode") + 1], "loopback")
        with mock.patch.object(fixture, "run_command", return_value=(0, b"", b"")) as command:
            self.ring.call("--import", data=b"synthetic-import")
        self.assertEqual(command.call_args.kwargs["input_bytes"], b"synthetic-import")
        self.assertNotIn("--passphrase-fd", command.call_args.args[0])

    def test_passphrase_rejects_bad_framing_types_or_simultaneous_data_before_command(self):
        invalid = (b"a" * 63, b"A" * 64, b"x" * 64, b"a" * 63 + b"\n",
                   b"a" * 63 + b"\r", b"a" * 63 + b"\x00", "a" * 64, bytes(range(64)))
        with mock.patch.object(fixture, "run_command") as command:
            for value in invalid:
                with self.subTest(kind=type(value).__name__), \
                        self.assertRaisesRegex(fixture.KitError, "^invalid_passphrase_transport$"):
                    self.ring.call("--export", passphrase=value)
            with self.assertRaisesRegex(fixture.KitError, "^invalid_passphrase_transport$"):
                self.ring.call("--export", passphrase=self.secret, data=b"synthetic-data")
        command.assert_not_called()

    def test_successful_decryption_requires_every_integrity_and_algorithm_marker(self):
        lines = self.diagnostics.splitlines()
        with mock.patch.object(fixture, "run_command", return_value=(0, fixture.CANARY, self.diagnostics)):
            self.assertEqual(self.ring.call("--decrypt"), fixture.CANARY)
        for missing in lines:
            diagnostics = b"\n".join(line for line in lines if line != missing)
            with self.subTest(missing=missing), \
                    mock.patch.object(fixture, "run_command", return_value=(0, fixture.CANARY, diagnostics)), \
                    self.assertRaisesRegex(fixture.KitError, "^decryption_integrity_unconfirmed$"):
                self.ring.call("--decrypt")

    def test_decryption_rejection_is_distinct_from_generic_command_failure(self):
        for diagnostics in (b"[GNUPG:] DECRYPTION_FAILED", b"[GNUPG:] BADMDC",
                            b"[GNUPG:] NO_SECKEY ABC", b"[GNUPG:] NODATA 1"):
            with mock.patch.object(fixture, "run_command", return_value=(1, b"private-output", diagnostics)), \
                    self.assertRaisesRegex(fixture.KitError, "^decryption_rejected$"):
                self.ring.call("--decrypt")
        with mock.patch.object(fixture, "run_command", return_value=(1, b"private-output", b"private-error")), \
                self.assertRaisesRegex(fixture.KitError, "^gpg_operation_failed$"):
            self.ring.call("--decrypt")

    def test_gpg_individual_output_and_diagnostic_bounds_remain_enforced(self):
        for output, diagnostics in ((b"x" * (fixture.MAX_KIT_BYTES + 1), b""),
                                    (b"", b"x" * (fixture.MAX_DIAGNOSTICS + 1))):
            with mock.patch.object(fixture, "run_command", return_value=(0, output, diagnostics)), \
                    self.assertRaisesRegex(fixture.KitError, "^gpg_operation_failed$"):
                self.ring.call("--export")

    def test_transform_uses_private_temporary_payload_and_fixed_symmetric_parameters(self):
        observed = []
        def call(*arguments, **kwargs):
            path = Path(arguments[-1])
            observed.append(path)
            self.assertEqual(path.read_bytes(), b"synthetic-package")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(kwargs, {"passphrase": self.secret})
            self.assertNotIn(self.secret.decode(), str(arguments))
            return b"synthetic-ciphertext"
        with mock.patch.object(self.ring, "call", side_effect=call) as command:
            self.assertEqual(fixture.transform(self.ring, b"synthetic-package", self.secret, encrypt=True),
                             b"synthetic-ciphertext")
        arguments = command.call_args.args
        for option, value in (("--cipher-algo", "AES256"), ("--s2k-mode", "3"),
                              ("--s2k-digest-algo", "SHA256"), ("--s2k-count", str(fixture.S2K_COUNT)),
                              ("--compress-algo", "none")):
            self.assertEqual(arguments[arguments.index(option) + 1], value)
        self.assertTrue(all(not path.parent.exists() for path in observed))

    def test_transform_bounds_and_failure_remove_its_temporary_payload(self):
        before = set(self.base.iterdir())
        with mock.patch.object(self.ring, "call") as command:
            for payload in (b"", "synthetic-string", b"x" * (fixture.MAX_KIT_BYTES + 1)):
                with self.assertRaisesRegex(fixture.KitError, "^transform_input_bound$"):
                    fixture.transform(self.ring, payload, self.secret)
        command.assert_not_called()
        with mock.patch.object(self.ring, "call", side_effect=fixture.KitError("decryption_rejected")), \
                self.assertRaisesRegex(fixture.KitError, "^decryption_rejected$"):
            fixture.transform(self.ring, b"synthetic-package", self.secret)
        self.assertEqual(set(self.base.iterdir()), before)

    def test_keyring_never_overwrites_an_existing_home_and_close_failures_are_visible(self):
        marker = self.ring.directory / "keep"
        marker.write_bytes(b"synthetic-existing")
        with self.assertRaises(FileExistsError):
            fixture.Keyring(self.ring.directory)
        self.assertEqual(marker.read_bytes(), b"synthetic-existing")
        for result in ((1, b"", b"private-error"), fixture.KitError("command_timeout")):
            arguments = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
            with mock.patch.object(fixture, "run_command", **arguments) as command:
                self.assertFalse(self.ring.close())
            self.assertEqual(command.call_args.kwargs, {"timeout": 5, "limit": fixture.MAX_DIAGNOSTICS})


class ControlFlowTests(unittest.TestCase):
    def test_negative_check_only_accepts_an_actual_decryption_rejection(self):
        self.assertTrue(fixture._must_fail(mock.Mock(side_effect=fixture.KitError("decryption_rejected"))))
        for code in ("command_timeout", "command_unavailable", "gpg_operation_failed",
                     "decryption_integrity_unconfirmed"):
            with self.subTest(code=code), self.assertRaisesRegex(fixture.KitError, f"^{code}$"):
                fixture._must_fail(mock.Mock(side_effect=fixture.KitError(code)))
        with self.assertRaisesRegex(RuntimeError, "synthetic-untrusted"):
            fixture._must_fail(mock.Mock(side_effect=RuntimeError("synthetic-untrusted")))
        with self.assertRaisesRegex(fixture.KitError, "^negative_check_unexpected_success$"):
            fixture._must_fail(lambda: b"unexpected-success")

    def test_cli_refuses_every_argument_before_any_operation_and_redacts_failures(self):
        for arguments in (["--help"], ["--source", "/synthetic-private-path"],
                          ["--passphrase", "synthetic-secret"], ["unexpected"]):
            with self.subTest(arguments=arguments), mock.patch.object(fixture, "roundtrip") as operation, \
                    contextlib.redirect_stdout(io.StringIO()) as output, \
                    contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(fixture.main(arguments), 2)
            operation.assert_not_called()
            self.assertNotIn("synthetic-secret", output.getvalue() + error.getvalue())
            self.assertNotIn("/synthetic-private-path", output.getvalue() + error.getvalue())
        with mock.patch.object(fixture, "roundtrip", side_effect=RuntimeError("synthetic-private-error")), \
                contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(fixture.main([]), 1)
        self.assertEqual(json.loads(error.getvalue()), {"error": "synthetic_emergency_kit_failed"})

    def test_cleanup_flags_cannot_report_cli_success(self):
        for agents, files in ((False, True), (True, False), (False, False)):
            report = {"agent_cleanup_confirmed": agents, "temporary_files_removed": files}
            with self.subTest(agents=agents, files=files), \
                    mock.patch.object(fixture, "roundtrip", return_value=report), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(fixture.main([]), 1)

    def test_early_roundtrip_failure_closes_all_mocked_rings_and_removes_temporary_tree(self):
        before = set(Path(tempfile.gettempdir()).glob("rbx-gpg-emergency-*"))
        rings = [mock.Mock() for _ in range(5)]
        rings[0].call.side_effect = fixture.KitError("gpg_operation_failed")
        rings[0].close.return_value = False
        with mock.patch.object(fixture, "Keyring", side_effect=rings), \
                mock.patch.object(fixture.subprocess, "Popen", side_effect=AssertionError("no real GPG")), \
                self.assertRaisesRegex(fixture.KitError, "^gpg_operation_failed$"):
            fixture.roundtrip()
        for ring in rings:
            ring.close.assert_called_once_with()
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("rbx-gpg-emergency-*")), before)


if __name__ == "__main__":
    unittest.main()
