"""Synthetic, offline checks for sandbox-only Secret staging."""

import base64
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "stage-satwake-sandbox-email-keys.py"
SPEC = importlib.util.spec_from_file_location("sandbox_email_keys", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def encoded(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


class FakeCommands:
    def __init__(self):
        self.secret = {
            "metadata": {"name": MODULE.SECRET_NAME, "namespace": MODULE.NAMESPACE,
                         "resourceVersion": "17"},
            "data": {
                "COMMS_API_URL": encoded(MODULE.INERT_COMMS_URL.encode()),
                "DATABASE_URL": encoded(b"synthetic-database-url"),
                "ASAAS_API_KEY": encoded(b"synthetic-asaas-key"),
                "UNRELATED_EXTENSION": encoded(b"synthetic-extra"),
            },
        }
        self.values = {entry: (b"a" if name == "COMMS_SERVICE_API_KEY" else b"b") * 64 + b"\n"
                       for name, entry in MODULE.PASS_ENTRIES.items()}
        self.calls = []
        self.fail_get = False
        self.concurrent_update = False
        self.patch = None

    def run(self, argv, *, input_bytes=None):
        self.calls.append(argv)
        if argv[0] == "pass":
            return self.values[argv[2]]
        if "get" in argv:
            if self.fail_get:
                raise MODULE.StagingError("synthetic get failure")
            return json.dumps(self.secret).encode()
        if "patch" in argv:
            self.patch = json.loads(input_bytes)
            if self.concurrent_update:
                self.secret["metadata"]["resourceVersion"] = "18"
            first, *adds = self.patch
            if (first != {"op": "test", "path": "/metadata/resourceVersion", "value": "17"}
                    or first["value"] != self.secret["metadata"]["resourceVersion"]):
                raise MODULE.StagingError("synthetic JSON Patch test failure")
            if len(adds) != 2 or any(item["op"] != "add" for item in adds):
                raise AssertionError("unexpected patch operations")
            for item in adds:
                name = item["path"].removeprefix("/data/")
                if name in self.secret["data"]:
                    raise AssertionError("unexpected overwrite")
            for item in adds:
                self.secret["data"][item["path"].removeprefix("/data/")] = item["value"]
            return b""
        raise AssertionError(f"unexpected command: {argv[0]}")


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeCommands()
        patcher = mock.patch.object(MODULE, "run_command", side_effect=self.fake.run)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_adds_only_two_keys_and_preserves_extras(self):
        before = self.fake.secret["data"].copy()
        MODULE.stage("/tmp/synthetic-kubeconfig")
        self.assertEqual({key: self.fake.secret["data"][key] for key in before}, before)
        self.assertEqual(set(self.fake.secret["data"]) - set(before), set(MODULE.PASS_ENTRIES))
        self.assertEqual(self.fake.patch[0]["path"], "/metadata/resourceVersion")
        self.assertEqual({op["path"] for op in self.fake.patch[1:]},
                         {"/data/COMMS_SERVICE_API_KEY", "/data/SATWAKE_EMAIL_SINK_OPERATOR_KEY"})
        for argv in self.fake.calls:
            self.assertNotIn("a" * 64, " ".join(argv))
            self.assertNotIn("b" * 64, " ".join(argv))

    def test_refuses_either_existing_key_before_reading_pass(self):
        for key in MODULE.PASS_ENTRIES:
            with self.subTest(key=key):
                fake = FakeCommands()
                fake.secret["data"][key] = encoded(b"existing")
                with mock.patch.object(MODULE, "run_command", side_effect=fake.run):
                    with self.assertRaises(MODULE.StagingError):
                        MODULE.stage("/tmp/synthetic-kubeconfig")
                self.assertEqual(len(fake.calls), 1)

    def test_refuses_noninert_source_url_before_reading_pass(self):
        self.fake.secret["data"]["COMMS_API_URL"] = encoded(b"http://production")
        with self.assertRaises(MODULE.StagingError):
            MODULE.stage("/tmp/synthetic-kubeconfig")
        self.assertEqual(len(self.fake.calls), 1)

    def test_refuses_failed_get_without_reading_pass(self):
        self.fake.fail_get = True
        with self.assertRaises(MODULE.StagingError):
            MODULE.stage("/tmp/synthetic-kubeconfig")
        self.assertEqual(len(self.fake.calls), 1)

    def test_refuses_invalid_or_identical_pass_entries(self):
        for replacement in (b"short\n", b"a" * 64 + b"\nextra\n", b"b" * 64 + b"\n"):
            with self.subTest(replacement=replacement[:5]):
                fake = FakeCommands()
                fake.values[MODULE.PASS_ENTRIES["COMMS_SERVICE_API_KEY"]] = replacement
                with mock.patch.object(MODULE, "run_command", side_effect=fake.run):
                    with self.assertRaises(MODULE.StagingError):
                        MODULE.stage("/tmp/synthetic-kubeconfig")
                self.assertIsNone(fake.patch)

    def test_resource_version_race_changes_nothing_else(self):
        before = self.fake.secret["data"].copy()
        self.fake.concurrent_update = True
        with self.assertRaises(MODULE.StagingError):
            MODULE.stage("/tmp/synthetic-kubeconfig")
        self.assertEqual(self.fake.secret["data"], before)
        self.assertEqual(self.fake.secret["metadata"]["resourceVersion"], "18")


class CommandOutputTests(unittest.TestCase):
    def test_command_suppresses_stderr_and_uses_stdin_for_patch(self):
        with mock.patch.object(MODULE.subprocess, "run") as run:
            run.return_value.stdout = b""
            MODULE.run_command(["kubectl", "patch"], input_bytes=b"synthetic-patch")
            kwargs = run.call_args.kwargs
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            self.assertEqual(kwargs["input"], b"synthetic-patch")
            self.assertEqual(kwargs["stdout"], subprocess.PIPE)


if __name__ == "__main__":
    unittest.main()
