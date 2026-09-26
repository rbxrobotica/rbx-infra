"""Offline failure and atomicity checks for sandbox source Secret staging."""

import base64
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
import uuid
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "stage-satwake-sandbox-core-secrets.py"
SPEC = importlib.util.spec_from_file_location("sandbox_core_secrets", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def encoded(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


class FakeCommands:
    def __init__(self):
        self.service_subject = "synthetic-sandbox-machine-subject"
        self.derived_tenant = str(uuid.uuid5(MODULE.TENANT_NAMESPACE, self.service_subject))
        self.secret = {
            "metadata": {"name": MODULE.SECRET_NAME, "namespace": MODULE.NAMESPACE,
                         "resourceVersion": "17"},
            "data": {
                **{name: encoded(f"synthetic-{name}".encode())
                   for name in MODULE.REQUIRED_EXISTING_KEYS},
                "COMMS_API_URL": encoded(MODULE.INERT_COMMS_URL.encode()),
                "UNRELATED_EXTENSION": encoded(b"synthetic-extra"),
            },
        }
        self.values = {
            MODULE.PASS_ENTRIES["ALTCHA_SECRET"]: b"a" * 64 + b"\n",
            MODULE.PASS_ENTRIES["COMMERCE_PUBLIC_TENANT_ID"]:
                self.derived_tenant.encode() + b"\n",
            MODULE.SERVICE_SUBJECT_ENTRY: self.service_subject.encode() + b"\n",
        }
        self.calls = []
        self.concurrent_update = False
        self.patch = None

    def run(self, argv, *, input_bytes=None):
        self.calls.append(argv)
        if argv[0] == "pass":
            if argv[2] not in self.values:
                raise MODULE.StagingError("synthetic missing pass entry")
            return self.values[argv[2]]
        if "get" in argv:
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

    def test_preserves_existing_keys_and_adds_only_new_properties(self):
        before = self.fake.secret["data"].copy()
        MODULE.stage("/tmp/synthetic-kubeconfig")
        self.assertEqual({key: self.fake.secret["data"][key] for key in before}, before)
        self.assertEqual(set(self.fake.secret["data"]) - set(before), set(MODULE.PASS_ENTRIES))
        self.assertEqual(self.fake.patch[0]["path"], "/metadata/resourceVersion")
        self.assertEqual({op["path"] for op in self.fake.patch[1:]},
                         {"/data/ALTCHA_SECRET", "/data/COMMERCE_PUBLIC_TENANT_ID"})
        self.assertEqual(base64.b64decode(self.fake.secret["data"]["COMMERCE_PUBLIC_TENANT_ID"]),
                         self.fake.derived_tenant.encode())
        for argv in self.fake.calls:
            self.assertNotIn("a" * 64, " ".join(argv))
            self.assertNotIn(self.fake.derived_tenant, " ".join(argv))
            self.assertNotIn(self.fake.service_subject, " ".join(argv))

    def test_refuses_existing_property_before_reading_pass(self):
        for key in MODULE.PASS_ENTRIES:
            with self.subTest(key=key):
                fake = FakeCommands()
                fake.secret["data"][key] = encoded(b"existing")
                with mock.patch.object(MODULE, "run_command", side_effect=fake.run):
                    with self.assertRaises(MODULE.StagingError):
                        MODULE.stage("/tmp/synthetic-kubeconfig")
                self.assertEqual(len(fake.calls), 1)

    def test_refuses_unsafe_source_before_reading_pass(self):
        for change in ("missing-email-key", "noninert-url", "wrong-namespace"):
            with self.subTest(change=change):
                fake = FakeCommands()
                if change == "missing-email-key":
                    fake.secret["data"].pop("COMMS_SERVICE_API_KEY")
                elif change == "noninert-url":
                    fake.secret["data"]["COMMS_API_URL"] = encoded(b"http://production")
                else:
                    fake.secret["metadata"]["namespace"] = "rbx-commerce"
                with mock.patch.object(MODULE, "run_command", side_effect=fake.run):
                    with self.assertRaises(MODULE.StagingError):
                        MODULE.stage("/tmp/synthetic-kubeconfig")
                self.assertEqual(len(fake.calls), 1)

    def test_refuses_bad_altcha_key_without_patching(self):
        for value in (b"short\n", b"A" * 64 + b"\n", b"a" * 64 + b"\nother\n"):
            with self.subTest(value=value[:5]):
                fake = FakeCommands()
                fake.values[MODULE.PASS_ENTRIES["ALTCHA_SECRET"]] = value
                with mock.patch.object(MODULE, "run_command", side_effect=fake.run):
                    with self.assertRaises(MODULE.StagingError):
                        MODULE.stage("/tmp/synthetic-kubeconfig")
                self.assertIsNone(fake.patch)

    def test_refuses_zero_production_random_v4_and_noncanonical_tenants(self):
        invalid = (
            b"00000000-0000-0000-0000-000000000000\n",
            b"885f24d2-1217-534e-bb1b-53440a3c04bb\n",
            b"aabbccdd-1234-4abc-8abc-123456789abc\n",
            b"aaaaaaaa-aaaa-5aaa-8aaa-aaaaaaaaaaaa\n",
            b"AABBCCDD-1234-4ABC-8ABC-123456789ABC\n",
            b"aabbccdd12344abc8abc123456789abc\n",
            b"aabbccdd-1234-4abc-8abc-123456789abc\nextra\n",
        )
        for value in invalid:
            with self.subTest(value=value[:8]):
                fake = FakeCommands()
                fake.values[MODULE.PASS_ENTRIES["COMMERCE_PUBLIC_TENANT_ID"]] = value
                with mock.patch.object(MODULE, "run_command", side_effect=fake.run):
                    with self.assertRaises(MODULE.StagingError):
                        MODULE.stage("/tmp/synthetic-kubeconfig")
                self.assertIsNone(fake.patch)

    def test_refuses_missing_or_changed_service_subject_without_patching(self):
        for subject in (None, b"different-subject\n", b"\n", b"subject\nextra\n"):
            with self.subTest(subject=subject):
                fake = FakeCommands()
                if subject is None:
                    fake.values.pop(MODULE.SERVICE_SUBJECT_ENTRY)
                else:
                    fake.values[MODULE.SERVICE_SUBJECT_ENTRY] = subject
                with mock.patch.object(MODULE, "run_command", side_effect=fake.run):
                    with self.assertRaises(MODULE.StagingError):
                        MODULE.stage("/tmp/synthetic-kubeconfig")
                self.assertIsNone(fake.patch)

    def test_resource_version_race_aborts_without_new_properties(self):
        before = self.fake.secret["data"].copy()
        self.fake.concurrent_update = True
        with self.assertRaises(MODULE.StagingError):
            MODULE.stage("/tmp/synthetic-kubeconfig")
        self.assertEqual(self.fake.secret["data"], before)


class CommandOutputTests(unittest.TestCase):
    def test_patch_uses_stdin_and_suppresses_diagnostics(self):
        with mock.patch.object(MODULE.subprocess, "run") as run:
            run.return_value.stdout = b""
            MODULE.run_command(["kubectl", "patch"], input_bytes=b"synthetic-patch")
            kwargs = run.call_args.kwargs
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            self.assertEqual(kwargs["input"], b"synthetic-patch")
            self.assertEqual(kwargs["stdout"], subprocess.PIPE)


if __name__ == "__main__":
    unittest.main()
