"""Offline privacy and completeness checks for the metadata-only capacity probe."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "irc_capacity_probe", Path(__file__).with_name("capacity_probe.py")
)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class CapacityProbeSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "irc"
        self.root.mkdir()
        self.root_patch = mock.patch.object(probe, "STATE_ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.execution_guard = mock.patch.object(
            probe.subprocess, "run", side_effect=AssertionError("external execution forbidden")
        )
        self.execution_guard.start()
        self.addCleanup(self.execution_guard.stop)

    def test_payloads_and_outside_symlinks_are_never_opened(self):
        config = self.root / "config"
        config.mkdir()
        payload = b"SYNTHETIC_PRIVATE_CONVERSATION"
        (config / "ergo_history.db").write_bytes(payload)
        (config / "ergo_history.db-wal").write_bytes(b"wal")
        (config / "ergo_history.db-shm").write_bytes(b"shm-data")
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "private.txt").write_bytes(b"OUTSIDE_PAYLOAD" * 100)
        (config / "outside-directory").symlink_to(outside, target_is_directory=True)
        (config / "outside-file").symlink_to(outside / "private.txt")

        with mock.patch("builtins.open", side_effect=AssertionError("payload opened")), \
                mock.patch("io.open", side_effect=AssertionError("payload opened")), \
                mock.patch("os.open", side_effect=AssertionError("payload opened")):
            report = probe.metadata_tree(config)

        self.assertTrue(report["complete"])
        self.assertEqual(report["regular_files"], 3)
        self.assertEqual(report["bytes"], len(payload) + 3 + 8)
        self.assertEqual(report["database_bytes"], len(payload))
        self.assertEqual(report["symlinks_skipped"], 2)
        serialized = json.dumps(report)
        self.assertNotIn(payload.decode(), serialized)
        self.assertNotIn(str(outside), serialized)

    def test_metadata_errors_are_incomplete_not_an_empty_success(self):
        good = self.root / "good.db"
        unavailable = self.root / "unavailable.db"
        good.write_bytes(b"visible-size")
        unavailable.write_bytes(b"not-readable")
        original_lstat = Path.lstat

        def selectively_unavailable(path, *args, **kwargs):
            if path == unavailable:
                raise PermissionError("SYNTHETIC_PRIVATE_ERROR_DETAIL")
            return original_lstat(path, *args, **kwargs)

        with mock.patch.object(Path, "lstat", selectively_unavailable):
            report = probe.metadata_tree(self.root)
        self.assertFalse(report["complete"])
        self.assertEqual(report["metadata_errors"], 1)
        self.assertEqual(report["regular_files"], 1)
        self.assertNotIn("SYNTHETIC_PRIVATE_ERROR_DETAIL", json.dumps(report))

        def failed_walk(path, *, followlinks, onerror):
            onerror(PermissionError("SYNTHETIC_PRIVATE_ERROR_DETAIL"))
            return iter(())

        with mock.patch.object(probe.os, "walk", side_effect=failed_walk):
            report = probe.metadata_tree(self.root)
        self.assertTrue(report["exists"])
        self.assertFalse(report["complete"])
        self.assertGreater(report["metadata_errors"], 0)

    def test_directory_and_file_fanout_both_stop_at_the_entry_bound(self):
        entries = [f"entry-{number}" for number in range(10001)]
        for directories, files in ((entries, []), ([], entries)):
            with self.subTest(kind="directories" if directories else "files"):
                with mock.patch.object(
                    probe.os, "walk", return_value=iter([(str(self.root), directories, files)])
                ):
                    report = probe.metadata_tree(self.root)
                self.assertFalse(report["complete"])
                self.assertTrue(report["entry_bound_reached"])
                self.assertEqual(report["regular_files"], 0)

    def test_selective_inspection_excludes_secrets_addresses_and_outside_mounts(self):
        config = self.root / "config"
        config.mkdir()
        secret_marker = "SYNTHETIC_SECRET_MUST_NOT_REACH_REPORT"
        fixture = {
            "State": {"Running": True, "Pid": 123, "StartedAt": "generation-one"},
            "RestartCount": 0,
            "Config": {
                "Image": "example.invalid/ergo@sha256:synthetic",
                "Env": ["PASSWORD=" + secret_marker],
                "Labels": {"private": secret_marker},
                "Cmd": [secret_marker],
            },
            "HostConfig": {"Memory": 0, "NanoCpus": 0, "NetworkMode": "bridge"},
            "NetworkSettings": {
                "Ports": {"6697/tcp": [{"HostIp": "100.64.23.45", "HostPort": "6697"}]}
            },
            "Mounts": [
                {"Source": str(config), "Destination": "/ircd"},
                {"Source": str(self.base / "outside"), "Destination": "/private"},
            ],
        }

        def formatted_inspect(args):
            self.assertEqual(args[:3], ["docker", "inspect", "--format"])
            self.assertEqual(args[-1], "rbx-ergo")
            selected = {}
            for key, expression in re.findall(r'"([a-z_]+)":\{\{json ([.\w]+)\}\}', args[3]):
                value = fixture
                for part in expression.lstrip(".").split("."):
                    value = value[part]
                selected[key] = value
            return json.dumps(selected)

        with mock.patch.object(probe, "command", side_effect=formatted_inspect):
            report = probe.inspect("rbx-ergo")

        serialized = json.dumps(report)
        self.assertNotIn(secret_marker, serialized)
        self.assertNotIn("100.64.23.45", serialized)
        self.assertNotIn(str(self.base), serialized)
        self.assertEqual(report["excluded_mount_count"], 1)
        self.assertTrue(report["coverage_incomplete"])
        self.assertEqual(report["state_mount_metadata"][0]["regular_files"], 0)
        self.assertEqual(
            report["published_ports"][0]["binding_class"], "cgnat_not_a_trust_assertion"
        )

    def run_main(self, *, inspection, command, extra_args=()):
        output = io.StringIO()
        with mock.patch.object(probe, "CONTAINERS", ("rbx-ergo",)), \
                mock.patch.object(probe, "inspect", return_value=inspection), \
                mock.patch.object(probe, "command", side_effect=command), \
                mock.patch(
                    "sys.argv",
                    ["capacity_probe.py", "--samples", "2", "--interval", "1", *extra_args],
                ), \
                contextlib.redirect_stdout(output):
            probe.main()
        return json.loads(output.getvalue())

    def test_changed_container_generation_cannot_contribute_a_sample(self):
        inspection = {"running": True, "pid": 123, "started_at": "generation-one"}
        replies = iter([
            json.dumps({"CPUPerc": "0.1%", "Name": "PRIVATE_UNSELECTED_STATS_FIELD"}),
            json.dumps({"pid": 123, "start": "generation-two"}),
        ])
        with mock.patch.object(probe, "sample", side_effect=AssertionError("wrong generation")), \
                mock.patch.object(probe.time, "sleep") as sleep:
            report = self.run_main(inspection=inspection, command=lambda args: next(replies))
        container = report["containers"]["rbx-ergo"]
        self.assertEqual(container["samples"], [])
        self.assertIn("observation_error", container)
        self.assertNotIn("PRIVATE_UNSELECTED_STATS_FIELD", json.dumps(report))
        sleep.assert_not_called()

    def test_denied_proc_fallback_and_explicit_helper_keep_reports_private(self):
        for extra_args, helper_output in (
            ((), None),
            (("--container-metadata",), "128 /ircd\n2"),
            (("--container-metadata",), "128 /ircd\n2\nPRIVATE_HELPER_DETAIL"),
        ):
            with self.subTest(flag=bool(extra_args), helper_output=helper_output):
                calls = []

                def selected_command(args):
                    calls.append(args)
                    if args[:2] == ["docker", "stats"]:
                        return json.dumps({
                            "CPUPerc": "0.1%", "MemUsage": "12MiB / 1GiB",
                            "Name": "PRIVATE_UNSELECTED_STATS_FIELD",
                        })
                    if args[:2] == ["docker", "inspect"]:
                        return json.dumps({"pid": 123, "start": "generation-one"})
                    if args[:2] == ["docker", "exec"]:
                        self.assertTrue(extra_args, "helper requires explicit opt-in")
                        self.assertEqual(sample.call_count, 2, "helper must follow timed samples")
                        self.assertEqual(args[:5], ["docker", "exec", "rbx-ergo", "/bin/sh", "-c"])
                        return helper_output
                    self.fail("unexpected external command shape")

                inspection = {"running": True, "pid": 123, "started_at": "generation-one"}
                with mock.patch.object(
                    probe, "sample", side_effect=PermissionError("PRIVATE_PROC_DETAIL")
                ) as sample, mock.patch.object(
                    probe, "metadata_tree", side_effect=PermissionError("PRIVATE_BACKUP_DETAIL")
                ), mock.patch.object(probe.time, "sleep"):
                    report = self.run_main(
                        inspection=inspection, command=selected_command, extra_args=extra_args
                    )

                container = report["containers"]["rbx-ergo"]
                self.assertEqual(container["docker_stats_baseline"]["MemUsage"], "12MiB / 1GiB")
                self.assertEqual(len(container["samples"]), 2)
                self.assertTrue(all(item["cgroup_available"] is False for item in container["samples"]))
                self.assertFalse(report["same_host_backup_metadata"]["complete"])
                serialized = json.dumps(report)
                for private in ("PRIVATE_PROC_DETAIL", "PRIVATE_BACKUP_DETAIL",
                                "PRIVATE_UNSELECTED_STATS_FIELD", "PRIVATE_HELPER_DETAIL"):
                    self.assertNotIn(private, serialized)
                if not extra_args:
                    self.assertFalse(any(args[:2] == ["docker", "exec"] for args in calls))
                    self.assertNotIn("container_helper_metadata", container)
                elif helper_output.endswith("PRIVATE_HELPER_DETAIL"):
                    self.assertIn("observation_error", container)
                    self.assertNotIn("container_helper_metadata", container)
                else:
                    self.assertEqual(container["container_helper_metadata"]["allocated_kib_ircd"], 128)
                    self.assertEqual(container["container_helper_metadata"]["established_irc_tcp_connections"], 2)


if __name__ == "__main__":
    unittest.main()
