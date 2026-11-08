"""Synthetic keys and a local inert socket; every external command is mocked."""

import base64
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "network" / "pass_ssh_agent.py"
SPEC = importlib.util.spec_from_file_location("pass_ssh_agent", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def public_key(suffix=b"synthetic-public"):
    algorithm = b"ssh-ed25519"
    blob = len(algorithm).to_bytes(4, "big") + algorithm + suffix
    return algorithm + b" " + base64.b64encode(blob) + b" fixture-comment\n"


class PassSSHAgentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir(mode=0o700)
        self.socket_path = self.runtime / "agent.sock"
        inert_socket = socket.socket(socket.AF_UNIX)
        inert_socket.bind(str(self.socket_path))
        self.addCleanup(inert_socket.close)
        self.socket_path.chmod(0o600)
        self.public_path = self.root / "identity.pub"
        self.public = public_key()
        self.public_path.write_bytes(self.public)
        self.public_path.chmod(0o600)
        self.private = (b"-----BEGIN OPENSSH PRIVATE KEY-----\n"
                        + base64.b64encode(b"openssh-key-v1\x00SYNTHETIC-PRIVATE-NEVER-PRINT")
                        + b"\n-----END OPENSSH PRIVATE KEY-----\n")
        self.fingerprint = b"SHA256:" + b"a" * 43
        self.key_line = b"256 " + self.fingerprint + b" fixture-comment (ED25519)\n"
        self.config = (f"host fixture\nidentityfile {self.public_path}\n"
                       f"identityagent {self.socket_path}\nstricthostkeychecking true\n"
                       "forwardagent no\nidentitiesonly yes\nhostname fixture.invalid\n")
        self.environment = {"XDG_RUNTIME_DIR": str(self.runtime),
                            "SSH_AUTH_SOCK": "/unrelated/socket",
                            "SSH_ASKPASS": "never-invoke-this"}
        self.present = False
        self.calls = []
        self.overrides = {}

    def runner(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if argv[0] in self.overrides:
            override = self.overrides[argv[0]]
            return override(argv, **kwargs) if callable(override) else override
        if argv[0] == MODULE.SSH:
            self.assertEqual(argv, [MODULE.SSH, "-G", "--", "fixture"])
            return 0, self.config.encode(), b""
        if argv[0] == MODULE.KEYGEN:
            if "-l" in argv:
                self.assertEqual(kwargs["input_bytes"], self.public)
                return 0, self.key_line, b""
            self.assertEqual(argv, [MODULE.KEYGEN, "-y", "-P", "", "-f", "/dev/stdin"])
            self.assertEqual(kwargs["input_bytes"], self.private)
            return 0, self.public, b""
        if argv[0] == MODULE.ADD:
            if "-l" in argv:
                return ((0, self.key_line, b"") if self.present else
                        (1, b"The agent has no identities.\n", b""))
            self.assertEqual(kwargs["input_bytes"], self.private)
            self.present = True
            return 0, b"", b"Identity added: secret-local-path\n"
        if argv[0] == MODULE.PASS:
            self.assertEqual(argv, [MODULE.PASS, "show", "--", "rbx/fixture/key"])
            return 0, b"fixture metadata\n" + self.private, b""
        self.fail("unexpected external command")

    def activate(self, action="load", **kwargs):
        return MODULE.activate(action, kwargs.pop("host", "fixture"),
                               kwargs.pop("pass_entry", "rbx/fixture/key" if action == "load" else None),
                               runner=self.runner, environment=self.environment, **kwargs)

    def commands(self, executable):
        return [argv for argv, _ in self.calls if argv[0] == executable]

    def assert_sanitized(self, report):
        encoded = json.dumps(report)
        for secret in (str(self.root), "fixture-comment", self.fingerprint.decode(),
                       "PRIVATE", "secret-local-path", "rbx/fixture/key"):
            self.assertNotIn(secret, encoded)
        self.assertEqual(set(report), {"code", "configuration_valid", "agent_available",
                                       "expected_key_loaded", "changed"})

    def test_status_missing_never_reads_pass_or_loads(self):
        report = self.activate("status")
        self.assertEqual(report["code"], "key_missing")
        self.assertTrue(report["agent_available"])
        self.assertFalse(report["changed"])
        self.assertEqual(self.commands(MODULE.PASS), [])
        self.assertEqual(self.commands(MODULE.ADD), [[MODULE.ADD, "-l", "-E", "sha256"]])
        self.assert_sanitized(report)

    def test_loaded_identity_is_noop_even_for_load(self):
        self.present = True
        report = self.activate()
        self.assertEqual(report["code"], "ready")
        self.assertFalse(report["changed"])
        self.assertEqual(self.commands(MODULE.PASS), [])
        self.assertEqual(len(self.commands(MODULE.KEYGEN)), 1)

    def test_current_user_tilde_identity_uses_passwd_home_not_environment(self):
        self.config = self.config.replace(str(self.public_path), "~/identity.pub")
        self.environment["HOME"] = str(self.root / "wrong-home")
        with mock.patch.object(MODULE.pwd, "getpwuid",
                               return_value=mock.Mock(pw_dir=str(self.root))) as passwd:
            report = self.activate("status")
        passwd.assert_called_once_with(os.getuid())
        self.assertEqual(report["code"], "key_missing")
        self.assertTrue(report["configuration_valid"])
        self.assertEqual(self.commands(MODULE.PASS), [])

    def test_other_user_tilde_and_expansion_tokens_are_rejected(self):
        original = self.config
        for value in ("~otheruser/identity.pub", "~/$HOME.pub", "~/%d.pub"):
            with self.subTest(value=value), \
                    mock.patch.object(MODULE.pwd, "getpwuid",
                                      return_value=mock.Mock(pw_dir=str(self.root))):
                self.config = original.replace(str(self.public_path), value)
                self.assertEqual(self.activate("status")["code"], "unsafe_path")
        self.assertEqual(self.commands(MODULE.PASS), [])

    def test_openssh_mixed_case_unknown_field_is_accepted(self):
        self.config += "canonicalizePermittedcnames none\n"
        self.assertEqual(self.activate("status")["code"], "key_missing")
        self.assertEqual(self.commands(MODULE.PASS), [])

    def test_case_variant_sensitive_field_duplicates_are_rejected(self):
        original = self.config
        for field, value in (("IdentityFile", str(self.public_path)),
                             ("IdentityAgent", str(self.socket_path)),
                             ("StrictHostKeyChecking", "true"),
                             ("ForwardAgent", "no"), ("IdentitiesOnly", "yes"),
                             ("Host", "fixture")):
            with self.subTest(field=field):
                self.config = original + f"{field} {value}\n"
                self.assertEqual(self.activate("status")["code"], "ambiguous_ssh_config")
        self.assertEqual(self.commands(MODULE.PASS), [])

    def test_match_gate_stdin_only_exact_agent_and_default_bounded_lifetime(self):
        report = self.activate()
        self.assertEqual(report["code"], "loaded")
        self.assertTrue(report["changed"])
        self.assertTrue(report["expected_key_loaded"])
        self.assertIn([MODULE.ADD, "-t", "900", "-"], self.commands(MODULE.ADD))
        for argv, kwargs in self.calls:
            self.assertNotIn(self.private.decode(), str(argv))
            self.assertNotIn(self.private.decode(), str(kwargs["env"]))
            self.assertNotIn("SSH_ASKPASS", kwargs["env"])
            self.assertEqual(kwargs["env"]["SSH_ASKPASS_REQUIRE"], "never")
            if argv[0] != MODULE.SSH:
                self.assertEqual(kwargs["env"]["SSH_AUTH_SOCK"], str(self.socket_path))
        pass_options = next(options for argv, options in self.calls if argv[0] == MODULE.PASS)
        self.assertEqual(pass_options["limit"], 65536)
        self.assertEqual(pass_options["timeout"], 30)
        self.assert_sanitized(report)

    def test_explicit_lifetime_bounds(self):
        for ttl in (60, 3600):
            with self.subTest(ttl=ttl):
                self.present = False
                self.calls.clear()
                self.assertEqual(self.activate(ttl=ttl)["code"], "loaded")
                self.assertIn([MODULE.ADD, "-t", str(ttl), "-"], self.commands(MODULE.ADD))
        for ttl in (0, 59, 3601, -1, True, "900"):
            self.calls.clear()
            self.assertEqual(self.activate(ttl=ttl)["code"], "invalid_arguments")
            self.assertEqual(self.calls, [])

    def test_mismatch_prevents_any_agent_load(self):
        def keygen(argv, **kwargs):
            return (0, self.key_line if "-l" in argv else public_key(b"different"), b"")
        self.overrides[MODULE.KEYGEN] = keygen
        report = self.activate()
        self.assertEqual(report["code"], "public_key_mismatch")
        self.assertFalse(report["changed"])
        self.assertFalse(any("-t" in argv for argv in self.commands(MODULE.ADD)))

    def test_malformed_multiple_and_oversized_private_blocks_never_load(self):
        for value in (b"not a key", self.private + self.private,
                      self.private.replace(b"b3Bl", b"!!!!"), b"x" * 65537,
                      self.private.replace(b"-----END", b"-----BROKEN")):
            with self.subTest(size=len(value)):
                self.calls.clear()
                self.overrides[MODULE.PASS] = (0, value, b"")
                self.assertEqual(self.activate()["code"], "invalid_private_key_block")
                self.assertFalse(any("-t" in argv for argv in self.commands(MODULE.ADD)))

    def test_encrypted_or_invalid_private_derivation_never_loads(self):
        self.overrides[MODULE.KEYGEN] = lambda argv, **_: (
            (0, self.key_line, b"") if "-l" in argv else
            (1, b"", self.private + b" private-path"))
        report = self.activate()
        self.assertEqual(report["code"], "private_key_derivation_failed")
        self.assertFalse(any("-t" in argv for argv in self.commands(MODULE.ADD)))
        self.assert_sanitized(report)

    def test_missing_agent_never_reads_pass(self):
        self.socket_path.unlink()
        self.assertEqual(self.activate()["code"], "agent_unavailable")
        self.assertEqual(self.commands(MODULE.PASS), [])

    def test_agent_connection_error_never_reads_pass_or_exposes_stderr(self):
        self.overrides[MODULE.ADD] = (2, b"", self.private + str(self.root).encode())
        report = self.activate()
        self.assertEqual(report["code"], "agent_unavailable")
        self.assertEqual(self.commands(MODULE.PASS), [])
        self.assert_sanitized(report)

    def test_socket_file_symlink_and_public_symlink_are_rejected(self):
        self.socket_path.unlink()
        self.socket_path.write_text("not an agent")
        self.assertEqual(self.activate()["code"], "unsafe_agent_socket")
        self.socket_path.unlink()
        self.socket_path.symlink_to(self.public_path)
        self.assertEqual(self.activate()["code"], "unsafe_agent_socket")
        self.socket_path.unlink()
        self.public_path.unlink()
        self.public_path.symlink_to(self.root / "absent")
        self.assertEqual(self.activate()["code"], "unsafe_public_key")

    def test_symlink_parent_and_insecure_runtime_are_rejected(self):
        link = self.root / "runtime-link"
        link.symlink_to(self.runtime, target_is_directory=True)
        self.environment["XDG_RUNTIME_DIR"] = str(link)
        self.config = self.config.replace(str(self.socket_path), str(link / "agent.sock"))
        self.assertEqual(self.activate()["code"], "unsafe_path")
        self.environment["XDG_RUNTIME_DIR"] = str(self.runtime)
        self.config = self.config.replace(str(link / "agent.sock"), str(self.socket_path))
        self.runtime.chmod(0o755)
        self.assertEqual(self.activate()["code"], "unsafe_agent_socket")

    def test_non_owned_public_and_socket_are_rejected(self):
        actual_fstat = os.fstat
        def foreign_public(descriptor):
            info = actual_fstat(descriptor)
            if info.st_ino == self.public_path.stat().st_ino:
                return mock.Mock(st_mode=info.st_mode, st_uid=os.getuid() + 1,
                                 st_size=info.st_size)
            return info
        with mock.patch.object(MODULE.os, "fstat", side_effect=foreign_public):
            self.assertEqual(self.activate()["code"], "unsafe_public_key")
        actual_stat = os.stat
        def foreign_socket(path, *args, **kwargs):
            info = actual_stat(path, *args, **kwargs)
            if path == "agent.sock":
                return mock.Mock(st_mode=info.st_mode, st_uid=os.getuid() + 1)
            return info
        with mock.patch.object(MODULE.os, "stat", side_effect=foreign_socket):
            self.assertEqual(self.activate()["code"], "unsafe_agent_socket")

    def test_duplicate_identity_relative_path_and_unsafe_policy_fail_closed(self):
        original = self.config
        for change, code in (
            (original + f"identityfile {self.public_path}\n", "ambiguous_ssh_config"),
            (original.replace(str(self.public_path), "./.ssh/key.pub"), "unsafe_path"),
            (original.replace(str(self.socket_path), "SSH_AUTH_SOCK"), "unsafe_path"),
            (original.replace("true", "accept-new"), "unsafe_ssh_policy"),
            (original.replace("forwardagent no", "forwardagent yes"), "unsafe_ssh_policy"),
            (original.replace("identitiesonly yes", "identitiesonly no"), "unsafe_ssh_policy"),
            (original.replace("host fixture\n", "host other\n"), "incomplete_ssh_config"),
            (original.replace("identityfile", "unrecognizedfield"), "incomplete_ssh_config"),
            (original.replace(".pub\n", "\n"), "public_identity_required"),
        ):
            with self.subTest(code=code):
                self.config = change
                self.assertEqual(self.activate()["code"], code)
                self.assertEqual(self.commands(MODULE.PASS), [])

    def test_outside_runtime_and_world_writable_public_fail_closed(self):
        self.environment["XDG_RUNTIME_DIR"] = str(self.root / "other-runtime")
        self.assertEqual(self.activate()["code"], "unsafe_agent_socket")
        self.environment["XDG_RUNTIME_DIR"] = str(self.runtime)
        self.public_path.chmod(0o666)
        self.assertEqual(self.activate()["code"], "unsafe_public_key")

    def test_untrusted_host_or_pass_reference_is_rejected_before_subprocess(self):
        for host in ("-oProxyCommand=bad", "a\nb", "user@host", "host;bad"):
            self.assertEqual(self.activate(host=host)["code"], "invalid_arguments")
        for entry in ("../key", "/key", "rbx/../key", "rbx//key", "rbx/-key", "a\nb"):
            self.assertEqual(self.activate(pass_entry=entry)["code"], "invalid_arguments")
        self.assertEqual(self.calls, [])

    def test_errors_and_argument_errors_never_print_inputs(self):
        self.overrides[MODULE.PASS] = (1, self.private, str(self.root).encode())
        self.assert_sanitized(self.activate())
        for args in (("load", "--host", "fixture", "--ttl", "PRIVATE"),
                     ("status", "--host", "fixture", "--unknown", "PRIVATE")):
            output, error = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                self.assertEqual(MODULE.main(args), 1)
            self.assertEqual(error.getvalue(), "")
            self.assert_sanitized(json.loads(output.getvalue()))

    def test_runner_exceptions_are_sanitized(self):
        def fail(*args, **kwargs):
            raise RuntimeError(self.private.decode())
        report = MODULE.activate("status", "fixture", runner=fail)
        self.assertEqual(report["code"], "operation_failed")
        self.assert_sanitized(report)

    def test_command_runner_uses_fixed_argv_pipes_and_no_shell(self):
        # Popen is mocked too: this test never invokes ssh, pass, or an agent.
        with mock.patch.object(MODULE.subprocess, "Popen", side_effect=FileNotFoundError) as popen:
            with self.assertRaisesRegex(MODULE.SafeError, "^command_unavailable$"):
                MODULE.run_command([MODULE.SSH, "-G", "--", "fixture"])
        self.assertFalse(popen.call_args.kwargs["shell"])
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(popen.call_args.kwargs["stdin"], MODULE.subprocess.DEVNULL)

    def fake_process(self, output):
        def stream(value):
            reader, writer = os.pipe()
            os.write(writer, value)
            os.close(writer)
            result = os.fdopen(reader, "rb", buffering=0)
            self.addCleanup(result.close)
            return result
        return mock.Mock(stdout=stream(output), stderr=stream(b""), stdin=None,
                         pid=123456789, wait=mock.Mock(return_value=0))

    def test_command_runner_caps_output_and_discards_raw_stderr(self):
        process = self.fake_process(b"SECRET-DATA")
        with mock.patch.object(MODULE.subprocess, "Popen", return_value=process), \
                mock.patch.object(MODULE.os, "killpg") as kill:
            with self.assertRaisesRegex(MODULE.SafeError, "^command_output_too_large$"):
                MODULE.run_command(["synthetic-only"], limit=8)
        kill.assert_called_once_with(process.pid, MODULE.signal.SIGKILL)
        process.wait.assert_called_once()

    def test_command_runner_deadline_kills_process_group(self):
        process = self.fake_process(b"")
        with mock.patch.object(MODULE.subprocess, "Popen", return_value=process), \
                mock.patch.object(MODULE.time, "monotonic", side_effect=[0, 11]), \
                mock.patch.object(MODULE.os, "killpg") as kill:
            with self.assertRaisesRegex(MODULE.SafeError, "^command_timeout$"):
                MODULE.run_command(["synthetic-only"], timeout=10)
        kill.assert_called_once_with(process.pid, MODULE.signal.SIGKILL)


if __name__ == "__main__":
    unittest.main()
