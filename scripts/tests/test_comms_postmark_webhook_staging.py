import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "postmark_staging", Path(__file__).parents[1] / "stage-comms-postmark-webhook-auth.py"
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
USERNAME = "rbx-postmark-0123456789abcdef"
PASSWORD = "SyntheticPassword" + "A" * (64 - len("SyntheticPassword"))
CREDENTIAL = {"username": USERNAME, "password": PASSWORD}
HASH_LINE = (USERNAME + ":$2y$12$" + "A" * 53 + "\n").encode()
CIPHERTEXT = b"\x85" + b"synthetic-encrypted-test-packet" * 4


def key_listing(fingerprint=module.RECIPIENT_FINGERPRINT, validity="-", expiry="", capabilities="scESC"):
    key = ["pub", validity, "4096", "1", "synthetic", "1", expiry, "", "", "", "", capabilities]
    fpr = ["fpr", "", "", "", "", "", "", "", "", fingerprint]
    return (":".join(key) + "\n" + ":".join(fpr) + "\n").encode()


class PostmarkStagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.kubeconfig = Path(self.temp.name) / "kubeconfig"
        self.kubeconfig.write_text("synthetic test file")
        self.kubeconfig.chmod(0o600)

    def test_existing_pass_entry_is_never_overwritten(self):
        with patch.object(module, "pass_path", return_value=self.kubeconfig), \
                patch.object(module, "run") as run:
            with self.assertRaises(module.ProvisioningError):
                module.generate()
            run.assert_not_called()

    def test_generate_stores_one_atomic_bundle_without_secret_arguments(self):
        missing = Path(self.temp.name) / "absent.gpg"
        with patch.object(module, "pass_path", return_value=missing), \
                patch.object(module, "validate_recipient") as validate, \
                patch.object(module.secrets, "token_hex", return_value="0123456789abcdef"), \
                patch.object(module.secrets, "token_urlsafe", return_value=PASSWORD), \
                patch.object(module, "run", return_value=CIPHERTEXT) as run:
            module.generate()
            validate.assert_called_once_with(missing)
            args, kwargs = run.call_args
            self.assertEqual(args[0][0], "gpg")
            self.assertEqual(args[0][-2:], ["--recipient", module.RECIPIENT_FINGERPRINT])
            self.assertIn("--no-options", args[0])
            self.assertIn("--no-encrypt-to", args[0])
            self.assertEqual(args[0][args[0].index("--trust-model") + 1], "always")
            self.assertEqual(json.loads(kwargs["data"]), CREDENTIAL)
            self.assertNotIn(PASSWORD, " ".join(args[0]))
            self.assertEqual(missing.read_bytes(), CIPHERTEXT)
            self.assertEqual(missing.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(missing.parent.glob(".postmark-encrypted-*")), [])

    def test_policy_resolution_requires_only_the_pinned_valid_encryption_key(self):
        root = Path(self.temp.name)
        (root / ".gpg-id").write_text("synthetic-policy-identity\n")
        target = root / "rbx/comms/new.gpg"
        for listing, accepted in [
            (key_listing(), True),
            (key_listing(fingerprint="A" * 40), False),
            (key_listing(validity="r"), False),
            (key_listing(validity="d"), False),
            (key_listing(expiry="1"), False),
            (key_listing(capabilities="scSC"), False),
            (key_listing(capabilities="scESCD"), False),
            (key_listing() + key_listing(), False),
        ]:
            with self.subTest(accepted=accepted, listing=listing[:12]), \
                    patch.object(module, "pass_store", return_value=root), \
                    patch.object(module, "run", return_value=listing):
                if accepted:
                    module.validate_recipient(target)
                else:
                    with self.assertRaises(module.ProvisioningError):
                        module.validate_recipient(target)

    def test_nearest_policy_cannot_add_another_recipient(self):
        root = Path(self.temp.name)
        nested = root / "rbx/comms"
        nested.mkdir(parents=True)
        (root / ".gpg-id").write_text(module.RECIPIENT_FINGERPRINT)
        (nested / ".gpg-id").write_text("first\nsecond\n")
        with patch.object(module, "pass_store", return_value=root), \
                patch.object(module, "run") as run:
            with self.assertRaises(module.ProvisioningError):
                module.validate_recipient(nested / "new.gpg")
            run.assert_not_called()

    def test_generation_leaves_unrelated_staged_index_untouched(self):
        root = Path(self.temp.name)
        index = root / ".git/index"
        index.parent.mkdir()
        index.write_bytes(b"synthetic unrelated staged index")
        target = root / "rbx/comms/new.gpg"
        with patch.object(module, "pass_path", return_value=target), \
                patch.object(module, "validate_recipient"), \
                patch.object(module, "run", return_value=CIPHERTEXT) as run:
            module.generate()
        self.assertEqual(index.read_bytes(), b"synthetic unrelated staged index")
        self.assertEqual([call.args[0][0] for call in run.call_args_list], ["gpg"])

    def test_atomic_publish_refuses_concurrent_creation_and_cleans_ciphertext_temp(self):
        target = Path(self.temp.name) / "new.gpg"
        original_link = os.link

        def race(source, destination):
            target.write_bytes(b"other actor's entry")
            original_link(source, destination)

        with patch.object(module.os, "link", side_effect=race):
            with self.assertRaises(module.ProvisioningError):
                module.publish_ciphertext(target, CIPHERTEXT)
        self.assertEqual(target.read_bytes(), b"other actor's entry")
        self.assertEqual(list(target.parent.glob(".postmark-encrypted-*")), [])

    def test_invalid_ciphertext_never_creates_a_file(self):
        target = Path(self.temp.name) / "new.gpg"
        with self.assertRaises(module.ProvisioningError):
            module.publish_ciphertext(target, json.dumps(CREDENTIAL).encode())
        self.assertFalse(target.exists())

    @patch.object(module.shutil, "which", return_value="/usr/bin/htpasswd")
    def test_existing_source_secret_aborts_before_decrypting_pass(self, _which):
        with patch.object(module, "run", return_value=b"secret/existing\n") as run:
            with self.assertRaises(module.ProvisioningError):
                module.stage(str(self.kubeconfig))
            self.assertEqual(run.call_count, 1)
            self.assertIn("--ignore-not-found", run.call_args.args[0])

    @patch.object(module.shutil, "which", return_value="/usr/bin/htpasswd")
    def test_source_creation_is_scoped_and_password_only_uses_stdin(self, _which):
        with patch.object(module, "run", side_effect=[
            b"", json.dumps(CREDENTIAL).encode(), HASH_LINE, b"secret/created\n"
        ]) as run:
            module.stage(str(self.kubeconfig))
        calls = run.call_args_list
        self.assertEqual(len(calls), 4)
        for call in calls:
            self.assertNotIn(PASSWORD, " ".join(call.args[0]))
        self.assertEqual(calls[2].args[0], ["htpasswd", "-niBC", "12", USERNAME])
        self.assertEqual(calls[2].kwargs["data"], PASSWORD.encode() + b"\n")
        self.assertEqual(calls[3].args[0][-3:], ["create", "-f", "-"])
        secret = json.loads(calls[3].kwargs["data"])
        self.assertEqual(secret["metadata"], {
            "name": "comms-postmark-webhook-auth", "namespace": "rbx-ia-br"
        })
        self.assertEqual(set(secret["stringData"]), {"username", "password", "htpasswd"})
        self.assertEqual(secret["stringData"]["htpasswd"], HASH_LINE.decode())

    @patch.object(module.shutil, "which", return_value="/usr/bin/htpasswd")
    def test_invalid_hash_cannot_reach_kubernetes_create(self, _which):
        with patch.object(module, "run", side_effect=[
            b"", json.dumps(CREDENTIAL).encode(), b"invalid or plaintext output"
        ]) as run:
            with self.assertRaises(module.ProvisioningError):
                module.stage(str(self.kubeconfig))
            self.assertEqual(run.call_count, 3)

    def test_invalid_credential_payloads_fail_closed(self):
        for value in [{**CREDENTIAL, "token": "unrelated"},
                      {**CREDENTIAL, "password": PASSWORD + "\n"},
                      {**CREDENTIAL, "username": "invalid:user"}, []]:
            with self.subTest(value_type=type(value).__name__), \
                    patch.object(module, "run", return_value=json.dumps(value).encode()):
                with self.assertRaises(module.ProvisioningError):
                    module.read_credential()

    def test_missing_htpasswd_does_not_read_credentials_or_write_secret(self):
        with patch.object(module.shutil, "which", return_value=None), \
                patch.object(module, "run") as run:
            with self.assertRaises(module.ProvisioningError):
                module.stage(str(self.kubeconfig))
            run.assert_not_called()

    def test_world_readable_kubeconfig_is_rejected(self):
        self.kubeconfig.chmod(0o644)
        with patch.object(module, "run") as run:
            with self.assertRaises(module.ProvisioningError):
                module.stage(str(self.kubeconfig))
            run.assert_not_called()

    def test_command_failure_cannot_expose_provider_credentials(self):
        error = subprocess.CalledProcessError(1, ["tool"], output=PASSWORD, stderr=PASSWORD)
        with patch.object(module.subprocess, "run", side_effect=error), \
                patch.object(module, "validate_recipient"), \
                contextlib.redirect_stderr(io.StringIO()) as stderr:
            with patch.object(module, "pass_path", return_value=Path(self.temp.name) / "absent"):
                self.assertEqual(module.main(["generate"]), 1)
        self.assertNotIn(PASSWORD, stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_command_timeout_is_bounded_and_redacted(self):
        error = subprocess.TimeoutExpired("tool", 60, output=PASSWORD)
        with patch.object(module.subprocess, "run", side_effect=error) as run:
            with self.assertRaises(module.ProvisioningError) as caught:
                module.run(["tool"], data=PASSWORD.encode())
            self.assertNotIn(PASSWORD, str(caught.exception))
            self.assertEqual(run.call_args.kwargs["timeout"], 60)
            self.assertEqual(run.call_args.kwargs["stderr"], subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()
