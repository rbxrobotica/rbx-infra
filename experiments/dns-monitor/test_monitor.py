"""Offline controller boundary tests; all endpoint tokens are dummy all-zero fixtures."""

import importlib.util
import pathlib
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


C = module("controller", "healthchecks-controller.py")
W = module("worker", "worker.py")
DUMMY = "00000000-0000-0000-0000-000000000000"


class MonitorBoundaries(unittest.TestCase):
    def test_plan_does_not_change_other_check(self):
        with mock.patch.object(C, "stored", return_value="dummy"), \
                mock.patch.object(C, "api", return_value={"checks": [{"name": "My First Check"}]}) as call:
            result = C.reconcile()
        self.assertTrue(result["change_needed"])
        self.assertEqual(result["other_checks_preserved"], 1)
        self.assertEqual(call.call_count, 1)

    def test_duplicate_owned_name_stops(self):
        with mock.patch.object(C, "stored", return_value="dummy"), \
                mock.patch.object(C, "api", return_value={"checks": [{"name": C.NAME}] * 2}):
            with self.assertRaisesRegex(ValueError, "duplicate"):
                C.reconcile(True)

    def test_foreign_check_with_same_name_is_not_adopted(self):
        with mock.patch.object(C, "stored", return_value="dummy"), \
                mock.patch.object(C, "api", return_value={"checks": [{"name": C.NAME, "slug": "other"}]}):
            with self.assertRaisesRegex(ValueError, "not_owned"):
                C.reconcile(True)

    def test_apply_creates_only_named_check_and_encrypts_only_ping(self):
        response = {"name": C.NAME, "ping_url": "https://hc-ping.com/" + DUMMY,
                    "channels": "dummy-existing-email"}
        with tempfile.TemporaryDirectory() as work, \
                mock.patch.object(C.pathlib.Path, "home", return_value=pathlib.Path(work)), \
                mock.patch.object(C, "stored", return_value="dummy"), \
                mock.patch.object(C, "api", side_effect=[{"checks": [{"name": "My First Check"}]}, response]) as call, \
                mock.patch.object(C.subprocess, "run") as encrypt:
            result = C.reconcile(True)
        self.assertEqual(call.call_args_list[1].args[2]["name"], C.NAME)
        self.assertEqual(call.call_args_list[1].args[2]["unique"], ["name"])
        self.assertEqual(encrypt.call_args.args[0][-1], C.PING_ENTRY)
        self.assertNotIn("ping_url", result)

    def test_missing_alert_integration_fails(self):
        with mock.patch.object(C, "stored", return_value="dummy"), \
                mock.patch.object(C, "api", side_effect=[{"checks": []},
                    {"name": C.NAME, "ping_url": "https://hc-ping.com/" + DUMMY, "channels": ""}]):
            with self.assertRaisesRegex(ValueError, "notification"):
                C.reconcile(True)

    def test_worker_refuses_other_ping_host(self):
        with self.assertRaisesRegex(ValueError, "destination"):
            W.heartbeat("https://example.com/" + DUMMY, False, "dummy")

    def test_redirect_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "redirect"):
            W.NoRedirect().redirect_request(None, None, None, None, None, None)


if __name__ == "__main__":
    unittest.main()
