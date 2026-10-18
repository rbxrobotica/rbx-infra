import copy
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    "mobile", Path(__file__).parents[1] / "network/irc_mobile_preflight.py")
mobile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mobile)


class MobilePreflightTests(unittest.TestCase):
    def model(self):
        return {"services": {"ergo": {"ports": [
            {"host_ip": ip, "published": str(pub), "target": target,
             "protocol": proto} for ip, pub, target, proto in mobile.EXPECTED_PORTS
        ]}}}

    def test_exact_private_bindings(self):
        mobile.check_model(self.model())

    def test_reject_public_missing_and_plaintext_mesh(self):
        for ip in (None, "0.0.0.0", "::", "13.140.148.30"):
            model = self.model()
            model["services"]["ergo"]["ports"][0]["host_ip"] = ip
            with self.assertRaises(ValueError):
                mobile.check_model(model)
        model = self.model()
        model["services"]["ergo"]["ports"].append(
            {"host_ip": "100.64.0.2", "published": "6667", "target": 6667})
        with self.assertRaises(ValueError):
            mobile.check_model(model)

    def test_preserve_loopback_and_reject_host_network(self):
        model = self.model()
        model["services"]["ergo"]["ports"].pop()
        with self.assertRaises(ValueError):
            mobile.check_model(model)
        model = self.model()
        model["services"]["ergo"]["network_mode"] = "host"
        with self.assertRaises(ValueError):
            mobile.check_model(model)

    def test_policy_preserves_existing_permissions(self):
        before = {"grants": [{"src": ["mint-admin@"],
                              "dst": ["corbetti-service@"], "ip": ["tcp:22"]}],
                  "tests": [{"src": "poco-rbx-trusted@", "deny": ["100.64.0.2:22"]}]}
        after = copy.deepcopy(before)
        after["grants"].append(copy.deepcopy(mobile.GRANT))
        mobile.check_policy(before, after)
        after["grants"][0]["ip"] = ["*"]
        with self.assertRaises(ValueError):
            mobile.check_policy(before, after)
        with self.assertRaises(ValueError):
            mobile.check_policy(before, {"grants": [mobile.GRANT]})

    def test_reject_duplicate_grant(self):
        with self.assertRaises(ValueError):
            mobile.check_policy({"grants": [mobile.GRANT]}, {"grants": [mobile.GRANT]})

    def test_real_certificate_trust_name_expiry_and_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            def run(*args):
                subprocess.run(["openssl", *args], cwd=p, check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            run("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "90",
                "-subj", "/CN=Ephemeral test CA", "-keyout", "ca.key", "-out", "ca.pem")
            run("req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj",
                "/CN=irc.rbxsystems.ch", "-keyout", "leaf.key", "-out", "leaf.csr")
            for name, days, valid in [("irc.rbxsystems.ch", "60", True),
                                      ("wrong.example", "60", False),
                                      ("irc.rbxsystems.ch", "1", False)]:
                (p / "ext").write_text("subjectAltName=DNS:" + name +
                                       "\nextendedKeyUsage=serverAuth\n")
                run("x509", "-req", "-in", "leaf.csr", "-CA", "ca.pem",
                    "-CAkey", "ca.key", "-CAcreateserial", "-days", days,
                    "-extfile", "ext", "-out", "leaf.pem")
                (p / "chain.pem").write_bytes((p / "leaf.pem").read_bytes() +
                                               (p / "ca.pem").read_bytes())
                args = [p / "chain.pem", p / "leaf.pem", p / "ca.pem", p / "ca.pem"]
                if valid:
                    mobile.check_certificate(*args)
                    with self.assertRaises(ValueError):
                        mobile.check_certificate(*args[:3], Path("/etc/ssl/certs/ca-certificates.crt"))
                    (p / "chain.pem").write_bytes((p / "ca.pem").read_bytes())
                    with self.assertRaises(ValueError):
                        mobile.check_certificate(*args)
                else:
                    with self.assertRaises(ValueError):
                        mobile.check_certificate(*args)


if __name__ == "__main__":
    unittest.main()
