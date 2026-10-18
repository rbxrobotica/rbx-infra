"""Synthetic offline adoption bundles; no live files or native Headscale."""

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "network" / "headscale_candidate.py"
SPEC = importlib.util.spec_from_file_location("headscale_candidate", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class HeadscaleCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sources = self.root / "sources"
        self.sources.mkdir(mode=0o700)
        self.parent = self.root / "prepared"
        self.parent.mkdir(mode=0o700)
        self.output = self.parent / "bundle"
        self.current = self.sources / "current.yaml"
        self.policy = self.sources / "policy-copy.json"
        self.candidate = self.sources / "candidate.yaml"
        self.reference = "/etc/headscale/synthetic-policy.hujson"
        self.secret = "SYNTHETIC-DO-NOT-PRINT-config-account-value"
        self.value = {
            "server_url": "https://synthetic.invalid",
            "grpc_allow_insecure": False,
            "policy": {"mode": "file", "path": self.reference},
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
            "unknown_existing_security": {
                "enabled": True,
                "count": 1,
                "targets": ["a", "b"],
            },
        }
        self.current.write_text(
            "# Preserve this exact original header.\n"
            + yaml.safe_dump(self.value, sort_keys=True),
            encoding="utf-8",
        )
        self.write_candidate(self.value)
        self.policy.write_bytes(
            (
                json.dumps(
                    {
                        "grants": [
                            {
                                "src": [self.secret],
                                "dst": ["tag:synthetic"],
                                "ip": ["tcp:22"],
                            }
                        ]
                    },
                    indent=2,
                )
                + "\n\n"
            ).encode()
        )

    def write_candidate(self, value):
        self.candidate.write_text(
            "# Different comments and key order are allowed.\n"
            + yaml.safe_dump(value, sort_keys=False),
            encoding="utf-8",
        )

    def arguments(self, **overrides):
        values = {
            "current_config": self.current,
            "policy": self.policy,
            "candidate_config": self.candidate,
            "expect_config_sha256": sha(self.current),
            "expect_policy_sha256": sha(self.policy),
            "output_dir": self.output,
            "policy_reference_path": self.reference,
        }
        values.update(overrides)
        return values

    def prepare(self, **overrides):
        return MODULE.prepare(**self.arguments(**overrides))

    def assert_sanitized(self, report):
        rendered = json.dumps(report)
        for value in (
            self.secret,
            str(self.root),
            self.reference,
            "synthetic.invalid",
            "tag:synthetic",
            "tcp:22",
        ):
            self.assertNotIn(value, rendered)

    def assert_no_output(self, report):
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(report["cleanup_complete"])
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.parent.iterdir()), [])
        self.assert_sanitized(report)

    def test_adoption_preserves_current_and_policy_bytes_without_rewriting_sources(
        self,
    ):
        inputs = (self.current, self.policy, self.candidate)
        before = {
            path: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ctime_ns)
            for path in inputs
        }
        self.assertNotEqual(self.current.read_bytes(), self.candidate.read_bytes())
        report = self.prepare()
        self.assertEqual(report["status"], "prepared")
        self.assertEqual(report["headscale_version"], "0.29.3")
        self.assertEqual(report["bundle_format"], "headscale-candidate-bundle-v1")
        self.assertTrue(report["adoption_only"])
        self.assertTrue(report["policy_preserved"])
        self.assertFalse(report["native_validation"])
        self.assertFalse(report["live_changes"])
        self.assertFalse(report["traffic_semantics_verified"])
        self.assertFalse(report["live_reference_path_verified"])
        self.assertTrue(report["snapshot_reference_override"])
        self.assertEqual(
            (self.output / "config.yaml").read_bytes(), before[self.current][0]
        )
        self.assertEqual(
            (self.output / "policy.hujson").read_bytes(), before[self.policy][0]
        )
        self.assertEqual(json.loads((self.output / "receipt.json").read_text()), report)
        self.assertEqual(report["hashes"]["current_config_sha256"], sha(self.current))
        self.assertEqual(report["hashes"]["bundled_config_sha256"], sha(self.current))
        self.assertEqual(
            report["hashes"]["candidate_config_sha256"], sha(self.candidate)
        )
        self.assertEqual(report["hashes"]["bundled_policy_sha256"], sha(self.policy))
        self.assertEqual(
            before,
            {
                path: (
                    path.read_bytes(),
                    path.stat().st_mtime_ns,
                    path.stat().st_ctime_ns,
                )
                for path in inputs
            },
        )
        self.assertEqual(
            {path.name for path in self.output.iterdir()},
            {"config.yaml", "policy.hujson", "receipt.json"},
        )
        self.assert_sanitized(report)

    def test_receipt_states_consistency_limit_without_claiming_attestation_was_proven(
        self,
    ):
        report = self.prepare()
        self.assertEqual(
            report["input_consistency"],
            {
                "operator_attestation_required": True,
                "stopped_sources_or_private_copies_required": True,
                "live_atomic_snapshot": False,
                "fingerprints_rechecked": True,
            },
        )

    def test_output_permissions_are_private_and_receipt_is_written_last(self):
        original = MODULE._write_file
        written = []

        def tracked(directory, name, data, created):
            written.append(name)
            return original(directory, name, data, created)

        with mock.patch.object(MODULE, "_write_file", side_effect=tracked):
            report = self.prepare()
        self.assertEqual(report["status"], "prepared")
        self.assertEqual(written, ["config.yaml", "policy.hujson", "receipt.json"])
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        for path in self.output.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.stat().st_uid, os.getuid())
            self.assertEqual(path.stat().st_nlink, 1)

    def test_invalid_or_mismatched_hash_never_creates_output(self):
        for name in ("expect_config_sha256", "expect_policy_sha256"):
            for value in (None, "", "bad", "g" * 64, "0" * 64):
                with self.subTest(name=name, value=value):
                    self.assert_no_output(self.prepare(**{name: value}))

    def test_full_identity_catches_changes_outside_preflight_selected_fields(self):
        variants = []
        for mutate in (
            lambda value: value.update(grpc_allow_insecure=True),
            lambda value: value.update(unexpected_security_key=True),
            lambda value: value.pop("unknown_existing_security"),
            lambda value: value["unknown_existing_security"].update(enabled=1),
            lambda value: value["unknown_existing_security"].update(count=True),
            lambda value: value["unknown_existing_security"].update(count=1.0),
            lambda value: value["unknown_existing_security"].update(targets=["b", "a"]),
        ):
            candidate = copy.deepcopy(self.value)
            mutate(candidate)
            variants.append(candidate)
        for value in variants:
            with self.subTest(value=value):
                self.write_candidate(value)
                report = self.prepare()
                self.assert_no_output(report)
                self.assertEqual(
                    report["error_code"], "adoption_requires_complete_semantic_identity"
                )

    def test_unknown_existing_fields_are_preserved_when_completely_identical(self):
        # Adoption cannot silently discard fields absent from the proposed IaC
        # template; a full, unchanged object can retain them without endorsing it.
        report = self.prepare()
        self.assertEqual(report["status"], "prepared")
        copied = yaml.safe_load((self.output / "config.yaml").read_text())
        self.assertEqual(
            copied["unknown_existing_security"], self.value["unknown_existing_security"]
        )

    def test_logical_reference_is_required_and_mismatch_is_blocking(self):
        for reference in (
            "relative",
            "/etc/headscale/../policy.hujson",
            "/etc/headscale/different.hujson",
        ):
            with self.subTest(reference=reference):
                self.assert_no_output(self.prepare(policy_reference_path=reference))

    def test_configured_logical_reference_is_never_opened(self):
        original = MODULE.preflight.read_file
        with mock.patch.object(MODULE.preflight, "read_file", wraps=original) as reader:
            report = self.prepare()
        self.assertEqual(report["status"], "prepared")
        self.assertEqual(
            {call.args[0] for call in reader.call_args_list},
            {self.current, self.policy, self.candidate},
        )

    def test_preflight_must_pass_before_any_output(self):
        for change in ("policy_removed", "derp_drift", "dns_drift"):
            with self.subTest(change=change):
                value = copy.deepcopy(self.value)
                if change == "policy_removed":
                    value.pop("policy")
                elif change == "derp_drift":
                    value["derp"]["server"]["enabled"] = False
                else:
                    value["dns"]["magic_dns"] = True
                self.write_candidate(value)
                report = self.prepare()
                self.assert_no_output(report)
                self.assertEqual(report["error_code"], "file_preflight_blocked")

    def test_bad_yaml_or_hujson_never_leaks_values_or_creates_output(self):
        self.candidate.write_text("secret: [" + self.secret)
        self.assert_no_output(self.prepare())
        self.write_candidate(self.value)
        self.policy.write_text("// " + self.secret + '\n{"grants": []}')
        self.assert_no_output(self.prepare())

    def test_existing_output_directory_file_and_symlink_are_untouched(self):
        for kind in ("directory", "file", "symlink"):
            with self.subTest(kind=kind):
                path = self.parent / kind
                if kind == "directory":
                    path.mkdir()
                    marker = path / "do-not-remove"
                    marker.write_text(self.secret)
                elif kind == "file":
                    path.write_text(self.secret)
                else:
                    path.symlink_to(self.current)
                before = path.lstat()
                report = self.prepare(output_dir=path)
                self.assertEqual(report["status"], "blocked")
                self.assertEqual(path.lstat().st_ino, before.st_ino)
                self.assertEqual(path.lstat().st_mtime_ns, before.st_mtime_ns)
                if kind == "directory":
                    self.assertEqual(marker.read_text(), self.secret)
                elif kind == "file":
                    self.assertEqual(path.read_text(), self.secret)
                else:
                    self.assertTrue(path.is_symlink())

    def test_parent_must_exist_be_owned_and_have_exact_0700_mode(self):
        missing = self.root / "does-not-exist" / "bundle"
        self.assertEqual(self.prepare(output_dir=missing)["status"], "blocked")
        self.assertFalse(missing.parent.exists())
        for mode in (0o755, 0o750, 0o770, 0o711):
            with self.subTest(mode=mode):
                self.parent.chmod(mode)
                self.assert_no_output(self.prepare())
        self.parent.chmod(0o700)
        original_uid = os.getuid()
        with mock.patch.object(MODULE.os, "getuid", return_value=original_uid + 1):
            self.assert_no_output(self.prepare())

    def test_symlinked_output_parent_and_symlinked_inputs_are_rejected(self):
        linked_parent = self.root / "linked-parent"
        linked_parent.symlink_to(self.parent, target_is_directory=True)
        report = self.prepare(output_dir=linked_parent / "bundle")
        self.assert_no_output(report)
        link = self.sources / "linked-current"
        link.symlink_to(self.current)
        self.assert_no_output(self.prepare(current_config=link))
        self.assertTrue(link.is_symlink())
        self.assertTrue(linked_parent.is_symlink())

    def test_inputs_cannot_be_contained_in_or_overlap_output(self):
        for output in (self.sources, self.current, self.current / "nested"):
            with self.subTest(output=output):
                report = self.prepare(output_dir=output)
                self.assertEqual(
                    report["error_code"], "output_contains_or_overlaps_input"
                )
                self.assertTrue(self.current.is_file())
                self.assertTrue(self.policy.is_file())
                self.assertFalse(self.output.exists())

    def test_input_change_after_audit_prevents_output(self):
        original = MODULE.preflight.audit

        def change_after_audit(*args, **kwargs):
            report = original(*args, **kwargs)
            self.current.write_bytes(self.current.read_bytes() + b"# changed\n")
            return report

        with mock.patch.object(
            MODULE.preflight, "audit", side_effect=change_after_audit
        ):
            report = self.prepare()
        self.assert_no_output(report)
        self.assertEqual(report["error_code"], "input_changed")

    def test_input_change_during_writes_cleans_up_new_bundle(self):
        original = MODULE._write_file

        def change_after_write(directory, name, data, created):
            original(directory, name, data, created)
            if name == "policy.hujson":
                self.candidate.write_bytes(self.candidate.read_bytes() + b"# changed\n")

        with mock.patch.object(MODULE, "_write_file", side_effect=change_after_write):
            report = self.prepare()
        self.assert_no_output(report)
        self.assertEqual(report["error_code"], "input_changed")

    def test_fingerprint_changes_are_detected_even_when_bytes_return_to_original(self):
        original = MODULE.preflight.audit

        def change_metadata(*args, **kwargs):
            report = original(*args, **kwargs)
            info = self.current.stat()
            os.utime(self.current, ns=(info.st_atime_ns, info.st_mtime_ns + 1000))
            return report

        with mock.patch.object(MODULE.preflight, "audit", side_effect=change_metadata):
            report = self.prepare()
        self.assert_no_output(report)
        self.assertEqual(report["error_code"], "input_changed")

    def test_write_failure_removes_only_new_files_and_sanitizes_errors(self):
        marker = self.parent / "existing-marker"
        marker.write_text(self.secret)
        original = MODULE._write_file

        def fail_after_first(directory, name, data, created):
            original(directory, name, data, created)
            raise RuntimeError(self.secret)

        with mock.patch.object(MODULE, "_write_file", side_effect=fail_after_first):
            report = self.prepare()
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(report["cleanup_complete"])
        self.assertFalse(self.output.exists())
        self.assertEqual(marker.read_text(), self.secret)
        self.assertEqual(list(self.parent.iterdir()), [marker])
        self.assert_sanitized(report)

    def test_final_input_check_failure_removes_receipt_too(self):
        original = MODULE._check_inputs
        calls = 0

        def fail_last(samples):
            nonlocal calls
            calls += 1
            original(samples)
            if calls == 3:
                raise MODULE.PreparationError("input_changed")

        with mock.patch.object(MODULE, "_check_inputs", side_effect=fail_last):
            report = self.prepare()
        self.assertEqual(calls, 3)
        self.assert_no_output(report)

    def test_cleanup_never_deletes_a_replacement_created_by_someone_else(self):
        original = MODULE._write_file
        foreign = self.root / "foreign"
        foreign.write_text("preserve unrelated content")

        def replace_then_fail(directory, name, data, created):
            original(directory, name, data, created)
            foreign.rename(self.output / name)
            raise RuntimeError(self.secret)

        with mock.patch.object(MODULE, "_write_file", side_effect=replace_then_fail):
            report = self.prepare()
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["cleanup_complete"])
        self.assertEqual(
            (self.output / "config.yaml").read_text(), "preserve unrelated content"
        )
        self.assert_sanitized(report)

    def test_no_process_or_network_is_invoked(self):
        import socket
        import subprocess

        with mock.patch.object(
            socket, "socket", side_effect=AssertionError("network forbidden")
        ), mock.patch.object(
            subprocess, "run", side_effect=AssertionError("process forbidden")
        ), mock.patch.object(
            subprocess, "Popen", side_effect=AssertionError("process forbidden")
        ), mock.patch.object(
            os, "system", side_effect=AssertionError("process forbidden")
        ):
            report = self.prepare()
        self.assertEqual(report["status"], "prepared")

    def test_cli_outputs_only_sanitized_json_and_requires_reference_argument(self):
        values = self.arguments()
        complete = []
        for name, value in values.items():
            complete.extend(("--" + name.replace("_", "-"), str(value)))
        incomplete = complete[:-2]
        for args, expected in (
            (incomplete, 2),
            (["--unknown=" + self.secret], 2),
            (complete, 0),
        ):
            with self.subTest(args=args):
                stdout, stderr = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
                    stderr
                ):
                    status = MODULE.main(args)
                self.assertEqual(status, expected)
                report = json.loads(stdout.getvalue())
                self.assertEqual(stderr.getvalue(), "")
                self.assert_sanitized(report)


if __name__ == "__main__":
    unittest.main()
