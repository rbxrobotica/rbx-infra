"""File-only synthetic fixtures for the Headscale observation tool."""

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "network" / "headscale_preflight.py"
SPEC = importlib.util.spec_from_file_location("headscale_preflight", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class HeadscalePreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.policy = self.root / "synthetic-policy.json"
        self.config = self.root / "synthetic-config.yaml"
        self.candidate = self.root / "synthetic-candidate.yaml"
        self.secret = "SYNTHETIC-DO-NOT-PRINT-secret-account-address"
        self.policy_value = {
            "groups": {"group:synthetic": [self.secret]},
            "grants": [
                {"src": ["group:synthetic"], "dst": ["tag:synthetic"], "ip": ["tcp:22"]}
            ],
        }
        self.config_value = {
            "policy": {"mode": "file", "path": str(self.policy)},
            "derp": {
                "server": {
                    "enabled": True,
                    "automatically_add_embedded_derp_region": True,
                },
                "urls": [],
                "paths": [],
                "auto_update_enabled": False,
            },
            "dns": {
                "magic_dns": False,
                "nameservers": {"global": []},
                "search_domains": [],
            },
            "noise": {"private_key_path": self.secret},
        }
        self.write_policy(self.policy_value)
        self.write_config(self.config_value)

    def write_policy(self, value):
        self.policy.write_text(json.dumps(value), encoding="utf-8")

    def write_config(self, value, path=None):
        (path or self.config).write_text(yaml.safe_dump(value), encoding="utf-8")

    def audit(self, candidate=None):
        return MODULE.audit(self.config, self.policy, candidate)

    def codes(self, report):
        return {item["code"] for item in report["findings"]}

    def assert_sanitized(self, report):
        encoded = json.dumps(report)
        for value in (
            self.secret,
            str(self.root),
            "group:synthetic",
            "tag:synthetic",
            "tcp:22",
        ):
            self.assertNotIn(value, encoded)

    def test_finite_grants_are_observation_only_never_traffic_authorization(self):
        report = self.audit()
        self.assertEqual(report["schema_version"], "headscale-file-preflight-v1")
        self.assertEqual(report["status"], "observation_only")
        self.assertTrue(report["observation_only"])
        self.assertFalse(report["deployment_verified"])
        self.assertFalse(report["traffic_semantics_verified"])
        self.assertFalse(report["mutations_performed"])
        observed = report["observations"]["policy"]
        self.assertEqual(observed["network_rule_count"], 1)
        self.assertFalse(observed["authorization_proven"])
        self.assert_sanitized(report)

    def test_all_read_fixtures_keep_bytes_hashes_and_mtimes(self):
        self.write_config(self.config_value, self.candidate)
        paths = (self.config, self.policy, self.candidate)
        before = {
            path: (
                path.read_bytes(),
                path.stat().st_mtime_ns,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            for path in paths
        }
        report = self.audit(self.candidate)
        self.assertEqual(report["status"], "observation_only")
        self.assertEqual(
            before,
            {
                path: (
                    path.read_bytes(),
                    path.stat().st_mtime_ns,
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
                for path in paths
            },
        )
        for label, path in zip(("config", "policy", "candidate_config"), paths):
            self.assertEqual(report["files"][label]["sha256"], before[path][2])
        self.assertEqual(set(self.root.iterdir()), set(paths))

    def test_no_process_socket_or_unrequested_file_is_accessed(self):
        import socket
        import subprocess

        with mock.patch.object(
            socket, "socket", side_effect=AssertionError("network forbidden")
        ), mock.patch.object(
            subprocess, "run", side_effect=AssertionError("process forbidden")
        ), mock.patch.object(
            subprocess, "Popen", side_effect=AssertionError("process forbidden")
        ):
            report = self.audit()
        self.assertEqual(report["status"], "observation_only")

    def test_missing_policy_reference_blocks_even_with_finite_file(self):
        for policy in (
            None,
            {},
            {"mode": "file"},
            {"path": str(self.policy)},
            {"mode": "database", "path": str(self.policy)},
        ):
            with self.subTest(policy=policy):
                current = copy.deepcopy(self.config_value)
                if policy is None:
                    current.pop("policy")
                else:
                    current["policy"] = policy
                self.write_config(current)
                report = self.audit()
                self.assertEqual(report["status"], "blocked")
                self.assert_sanitized(report)

    def test_policy_path_mismatch_is_not_opened_or_printed(self):
        current = copy.deepcopy(self.config_value)
        current["policy"]["path"] = "/nonexistent/" + self.secret
        self.write_config(current)
        report = self.audit()
        self.assertIn("config_policy_path_mismatch_or_relative", self.codes(report))
        self.assertTrue(report["files"]["policy"]["parsed"])
        self.assert_sanitized(report)

    def test_missing_empty_or_containerless_policy_is_potentially_permissive(self):
        for value in (None, "", "   \n", "{}", '{"groups": {}}'):
            with self.subTest(value=value):
                if value is None:
                    self.policy.unlink(missing_ok=True)
                else:
                    self.policy.write_text(value, encoding="utf-8")
                report = self.audit()
                self.assertEqual(report["status"], "blocked")
                self.assertTrue(
                    report["observations"]["policy"]["potential_permissive_default"]
                )
                self.assert_sanitized(report)

    def test_explicit_empty_rules_require_review_without_claiming_allow_all(self):
        self.write_policy({"acls": []})
        report = self.audit()
        flags = report["observations"]["policy"]
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(flags["explicit_empty_network_rules"])
        self.assertFalse(flags["potential_permissive_default"])

    def test_unsupported_hujson_duplicate_keys_and_nonfinite_json_block(self):
        for text in (
            "// " + self.secret + '\n{"acls": []}',
            '{"acls": [],}',
            '{"acls": [], "acls": [{}]}',
            '{"grants": NaN}',
            '{"grants": Infinity}',
            '["' + self.secret + '"]',
        ):
            with self.subTest(text=text):
                self.policy.write_text(text, encoding="utf-8")
                report = self.audit()
                self.assertEqual(report["status"], "blocked")
                self.assertFalse(report["files"]["policy"]["parsed"])
                self.assert_sanitized(report)

    def test_bad_yaml_alias_duplicate_and_secret_parser_errors_are_sanitized(self):
        for text in (
            "secret: [" + self.secret,
            "policy: {}\npolicy: {}",
            "secret: &s [*s]",
            "secret: !!python/object:synthetic {}",
            self.secret,
        ):
            with self.subTest(text=text):
                self.config.write_text(text, encoding="utf-8")
                report = self.audit()
                self.assertEqual(report["status"], "blocked")
                self.assertFalse(report["files"]["config"]["parsed"])
                self.assert_sanitized(report)

    def test_yaml_nesting_limit_is_blocking(self):
        self.config.write_text("secret: " + "[" * 100 + "0" + "]" * 100)
        report = self.audit()
        self.assertEqual(report["status"], "blocked")
        self.assertIn("config_unsupported_yaml_structure", self.codes(report))

    def test_rule_containers_and_rule_fields_are_checked(self):
        malformed = (
            {"acls": {}},
            {"grants": self.secret},
            {"grants": [self.secret]},
            {"acls": [{"action": "accept", "src": [], "dst": ["tag:synthetic:22"]}]},
            {
                "acls": [
                    {
                        "action": "deny",
                        "src": ["group:synthetic"],
                        "dst": ["tag:synthetic:22"],
                    }
                ]
            },
            {
                "grants": [
                    {
                        "src": ["group:synthetic"],
                        "dst": ["tag:synthetic"],
                        "ip": "tcp:22",
                    }
                ]
            },
            {"grants": [{"src": [123], "dst": ["tag:synthetic"], "ip": ["tcp:22"]}]},
        )
        for value in malformed:
            with self.subTest(value=value):
                self.write_policy(value)
                report = self.audit()
                self.assertEqual(report["status"], "blocked")
                self.assertIn(
                    "policy_network_rule_container_malformed_or_unsupported",
                    self.codes(report),
                )
                self.assert_sanitized(report)

    def test_obvious_broad_wildcard_rules_are_blocking_observations(self):
        self.write_policy(
            {"acls": [{"action": "accept", "src": ["*"], "dst": ["*:*"]}]}
        )
        report = self.audit()
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(
            report["observations"]["policy"]["broad_wildcard_rule_observed"]
        )
        self.assertFalse(report["traffic_semantics_verified"])

    def test_regular_file_symlink_parent_symlink_and_fifo_checks(self):
        original = self.policy.read_bytes()
        for kind in ("directory", "symlink", "parent_symlink", "fifo"):
            with self.subTest(kind=kind):
                candidate = self.root / ("candidate-" + kind)
                if kind == "directory":
                    candidate.mkdir()
                elif kind == "symlink":
                    candidate.symlink_to(self.policy)
                elif kind == "parent_symlink":
                    candidate.symlink_to(self.root, target_is_directory=True)
                    candidate = candidate / self.policy.name
                else:
                    os.mkfifo(candidate)
                report = MODULE.audit(self.config, candidate)
                self.assertEqual(report["status"], "blocked")
                self.assertFalse(report["files"]["policy"]["read"])
        self.assertEqual(original, self.policy.read_bytes())

    def test_size_cap_applies_before_parsing_every_input(self):
        for label, path in (
            ("config", self.config),
            ("policy", self.policy),
            ("candidate_config", self.candidate),
        ):
            with self.subTest(label=label):
                self.write_config(self.config_value)
                self.write_policy(self.policy_value)
                path.write_bytes(b"x" * (MODULE.MAX_FILE_BYTES + 1))
                report = self.audit(
                    self.candidate if label == "candidate_config" else None
                )
                self.assertIn(label + "_file_too_large", self.codes(report))
                self.assertIsNone(report["files"][label]["sha256"])

    def test_exact_file_size_limit_is_accepted(self):
        original = self.policy.read_bytes()
        self.policy.write_bytes(
            original + b" " * (MODULE.MAX_FILE_BYTES - len(original))
        )
        report = self.audit()
        self.assertEqual(report["status"], "observation_only")
        self.assertEqual(report["files"]["policy"]["size_bytes"], MODULE.MAX_FILE_BYTES)

    def test_concurrently_changed_file_is_unknown_and_blocking(self):
        original_read = MODULE.os.read
        changed = False

        def read_then_change(fd, size):
            nonlocal changed
            data = original_read(fd, size)
            if not changed:
                changed = True
                self.config.write_text("changed: synthetic\n", encoding="utf-8")
            return data

        with mock.patch.object(MODULE.os, "read", side_effect=read_then_change):
            report = self.audit()
        self.assertEqual(report["status"], "blocked")
        self.assertIn("config_file_changed", self.codes(report))
        self.assertIsNone(report["files"]["config"]["sha256"])

    def test_embedded_public_derp_and_dns_flags_without_values(self):
        current = copy.deepcopy(self.config_value)
        current["derp"]["urls"] = ["https://controlplane.tailscale.com/derpmap/default"]
        current["dns"]["override_local_dns"] = True
        current["dns"]["nameservers"] = {
            "global": ["192.0.2.1"],
            "split": {"synthetic.internal": ["192.0.2.2"]},
        }
        self.write_config(current)
        report = self.audit()
        flags = report["observations"]["config"]
        self.assertTrue(flags["derp"]["embedded_enabled"])
        self.assertTrue(flags["derp"]["tailscale_public_map_configured"])
        self.assertTrue(flags["dns"]["override_local_dns"])
        self.assertTrue(flags["dns"]["global_nameservers_configured"])
        self.assertNotIn("192.0.2.1", json.dumps(report))
        self.assertNotIn("tailscale.com", json.dumps(report))
        self.assert_sanitized(report)

    def test_string_booleans_and_malformed_profiles_are_unknown_not_false(self):
        current = copy.deepcopy(self.config_value)
        current["derp"]["server"]["enabled"] = "false"
        current["derp"]["urls"] = self.secret
        current["dns"]["magic_dns"] = "false"
        self.write_config(current)
        report = self.audit()
        self.assertEqual(report["status"], "blocked")
        flags = report["observations"]["config"]
        self.assertIsNone(flags["derp"]["embedded_enabled"])
        self.assertIsNone(flags["derp"]["external_map_urls_configured"])
        self.assertIsNone(flags["dns"]["magic_dns"])

    def test_candidate_reproduces_template_dropping_deployed_policy(self):
        desired = copy.deepcopy(self.config_value)
        desired.pop("policy")
        self.write_config(desired, self.candidate)
        report = self.audit(self.candidate)
        comparison = report["observations"]["comparison"]
        self.assertFalse(comparison["policy_reference_preserved"])
        self.assertFalse(comparison["derp_profile_drift"])
        self.assertFalse(comparison["dns_flags_or_profile_drift"])
        self.assertEqual(report["status"], "blocked")
        self.assertIn("candidate_policy_reference_not_preserved", self.codes(report))
        self.assert_sanitized(report)

    def test_candidate_derp_and_dns_drift_blocks_without_showing_addresses(self):
        desired = copy.deepcopy(self.config_value)
        desired["derp"]["urls"] = ["https://example.invalid/" + self.secret]
        desired["dns"]["nameservers"]["global"] = ["192.0.2.45"]
        self.write_config(desired, self.candidate)
        report = self.audit(self.candidate)
        self.assertEqual(report["status"], "blocked")
        comparison = report["observations"]["comparison"]
        self.assertTrue(comparison["policy_reference_preserved"])
        self.assertTrue(comparison["derp_profile_drift"])
        self.assertTrue(comparison["dns_flags_or_profile_drift"])
        self.assertNotIn("192.0.2.45", json.dumps(report))
        self.assert_sanitized(report)

    def test_cli_always_emits_json_and_nonzero_for_unsafe_or_unknown(self):
        for args, expected in (
            (["--config", str(self.config), "--policy", str(self.policy)], 0),
            (["--config", self.secret], 2),
            (
                [
                    "--config",
                    str(self.config),
                    "--policy",
                    str(self.policy),
                    "--unexpected=" + self.secret,
                ],
                2,
            ),
        ):
            with self.subTest(args=args):
                output, error = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(
                    error
                ):
                    status = MODULE.main(args)
                self.assertEqual(status, expected)
                report = json.loads(output.getvalue())
                self.assertEqual(error.getvalue(), "")
                self.assert_sanitized(report)

    def test_unexpected_exception_is_sanitized_and_blocking(self):
        with mock.patch.object(
            MODULE, "_config", side_effect=RuntimeError(self.secret)
        ):
            report = self.audit()
        self.assertEqual(report["status"], "blocked")
        self.assertIn("observation_failed", self.codes(report))
        self.assert_sanitized(report)


if __name__ == "__main__":
    unittest.main()
