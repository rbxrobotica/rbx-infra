import argparse
import base64
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "acme", Path(__file__).parents[1] / "network/irc_acme.py")
acme = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acme)


class AcmeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rbx-acme-test-", dir=Path.home())
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "staging").mkdir(mode=0o700)
        (root / "production").mkdir(mode=0o700)
        binary = root / "lego"
        binary.write_text("synthetic executable, never executed\n")
        binary.chmod(0o700)
        secret = root / "tsig"
        secret.write_bytes(base64.b64encode(b"synthetic-test-only-never-a-real-key") + b"\n")
        secret.chmod(0o600)
        self.args = argparse.Namespace(lego=binary,
            sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
            state=root, tsig_secret=secret, email="operator@example.test",
            environment="staging", operation="issue", execute=False)

    def test_default_does_not_read_credentials_or_execute(self):
        self.args.tsig_secret.unlink()
        with patch.object(acme.subprocess, "run") as run:
            self.assertEqual(acme.run(self.args)["status"], "plan_only")
            run.assert_not_called()

    def test_bad_binary_digest_stops_before_execution(self):
        self.args.execute = True
        self.args.sha256 = "0" * 64
        with patch.object(acme.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                acme.run(self.args)
            run.assert_not_called()

    def test_blank_credential_cannot_disable_tsig(self):
        self.args.execute = True
        self.args.tsig_secret.write_text("\n")
        with patch.object(acme.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                acme.run(self.args)
            run.assert_not_called()

    def test_private_credential_and_no_symlinks(self):
        self.args.execute = True
        self.args.tsig_secret.chmod(0o644)
        with self.assertRaises(ValueError):
            acme.run(self.args)
        self.args.tsig_secret.chmod(0o600)
        link = self.args.state / "link"
        link.symlink_to(self.args.tsig_secret)
        self.args.tsig_secret = link
        with self.assertRaises(ValueError):
            acme.run(self.args)

    def test_clean_environment_no_secret_in_argv_no_deployment(self):
        self.args.execute = True
        with patch.dict(os.environ, {"PDNS_API_KEY": "synthetic-hostile",
                                    "LEGO_SERVER": "https://invalid.example"}):
            with patch.object(acme.subprocess, "run") as run:
                run.return_value.returncode = 0
                result = acme.run(self.args)
                argv = run.call_args.args[0]
                env = run.call_args.kwargs["env"]
                self.assertIn(acme.SERVERS["staging"], argv)
                self.assertNotIn("PDNS_API_KEY", env)
                self.assertNotIn("LEGO_SERVER", env)
                self.assertNotIn("DNSUPDATE_TSIG_SECRET", env)
                self.assertEqual(env["DNSUPDATE_TSIG_SECRET_FILE"], str(self.args.tsig_secret))
                self.assertNotIn("synthetic-test-only", " ".join(argv))
                self.assertEqual(argv[-1], "run")
                self.assertFalse(result["deploys_certificate"])

    def test_renew_requires_existing_cert_in_selected_environment(self):
        self.args.execute = True
        self.args.operation = "renew"
        with self.assertRaises(ValueError):
            acme.run(self.args)
        certs = self.args.state / "staging/certificates"
        certs.mkdir()
        (certs / (acme.DOMAIN + ".crt")).write_text("fixture only")
        self.args.environment = "production"
        with self.assertRaises(ValueError):
            acme.run(self.args)

    def test_failure_does_not_claim_deployment(self):
        self.args.execute = True
        with patch.object(acme.subprocess, "run") as run:
            run.return_value.returncode = 1
            with self.assertRaises(ValueError):
                acme.run(self.args)

    def test_locked_issuer_does_not_start_second_process(self):
        self.args.execute = True
        with patch.object(acme.fcntl, "flock", side_effect=BlockingIOError):
            with patch.object(acme.subprocess, "run") as run:
                with self.assertRaises(BlockingIOError):
                    acme.run(self.args)
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
