"""Client refusal tests; fake review decisions are not server-policy coverage."""

import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import Mock, patch
import uuid

import thalamus_probe as probe


def allowed():
    ids = {key: str(uuid.uuid4()) for key in ("trace_id", "audit_id")}
    request = probe.synthetic_request()
    return {**ids, "decision": "Allow", "policy_id": "synthetic-policy",
            "review_reason": None, "policy_ref": None,
            "envelope": {**ids, "backend_handle_id": probe.BACKEND["id"],
                         "prompt": request["prompt"], "policy_ref": "synthetic-policy",
                         "budget_max_tokens": probe.BUDGET["max_tokens"],
                         "budget_max_latency_ms": probe.BUDGET["max_latency_ms"]}}


class ClientRefusalTests(unittest.TestCase):
    def assert_refused(self, pre, request=None):
        executor = probe.MockExecutor()
        with self.assertRaises((probe.ProbeFailure, ValueError, TypeError, KeyError)):
            executor.execute(pre, request or probe.synthetic_request(), "synthetic-policy")
        self.assertEqual(executor.calls, 0)

    def test_allow_executes_once_with_matching_audit_id(self):
        pre, executor = allowed(), probe.MockExecutor()
        result = executor.execute(pre, probe.synthetic_request(), "synthetic-policy")
        self.assertEqual(executor.calls, 1)
        self.assertEqual(result["audit_id"], pre["audit_id"])
        self.assertEqual(result["content"], "synthetic: ok")

    def test_every_non_allow_decision_refused_even_with_valid_envelope(self):
        for decision in ("Deny", "AllowWithReview", "unknown", "allow", "", None, True):
            with self.subTest(decision=decision):
                pre = allowed()
                pre["decision"] = decision
                self.assert_refused(pre)

    def test_missing_envelope_and_review_annotation_refused(self):
        for key, value in (("envelope", None), ("review_reason", "review"),
                           ("policy_ref", "review"), ("policy_id", "other")):
            with self.subTest(key=key):
                pre = allowed()
                pre[key] = value
                self.assert_refused(pre)

    def test_each_envelope_field_mismatch_refused(self):
        for key in allowed()["envelope"]:
            with self.subTest(key=key):
                pre = allowed()
                pre["envelope"][key] = "mismatch"
                self.assert_refused(pre)

    def test_invalid_correlation_ids_refused(self):
        for invalid in ("", "not-a-uuid", str(uuid.UUID(int=0)), None):
            pre = allowed()
            pre["audit_id"] = pre["envelope"]["audit_id"] = invalid
            self.assert_refused(pre)
        pre = allowed()
        pre["audit_id"] = pre["envelope"]["audit_id"] = pre["trace_id"]
        self.assert_refused(pre)

    def test_invalid_request_refused_despite_server_allow(self):
        for field in ("tenant", "product", "workflow", "requested_backend", "budget_hint"):
            with self.subTest(field=field):
                request = probe.synthetic_request()
                request[field] = "unpermitted"
                self.assert_refused(allowed(), request)
        for field in probe.BUDGET:
            request = probe.synthetic_request()
            request["budget_hint"][field] += 1
            self.assert_refused(allowed(), request)

    def test_timeout_closes_connection_and_cannot_execute(self):
        conn = Mock()
        conn.getresponse.side_effect = TimeoutError()
        executor = probe.MockExecutor()
        with patch.object(probe.http.client, "HTTPConnection", return_value=conn) as factory:
            with self.assertRaises(TimeoutError):
                pre = probe.LocalClient(12345, time.monotonic() + 15).request(
                    "/v1/pre-call", probe.synthetic_request())[1]
                executor.execute(pre, probe.synthetic_request(), "synthetic-policy")
        self.assertEqual(factory.call_args.args, ("127.0.0.1", 12345))
        conn.close.assert_called_once()
        self.assertEqual(executor.calls, 0)

    def test_deadline_and_disallowed_routes_refused_before_connecting(self):
        with patch.object(probe.http.client, "HTTPConnection") as factory:
            with self.assertRaises(TimeoutError):
                probe.LocalClient(12345, 0).request("/readyz")
            for path in ("/v1/call", "http://example.invalid", "/rbx/v1/sessions"):
                with self.assertRaises(probe.ProbeFailure):
                    probe.LocalClient(12345, time.monotonic() + 1).request(path)
        factory.assert_not_called()

    def test_configured_services_fail_readiness(self):
        ready = {"ok": True, "backend_configured": False, "durable_audit": False,
                 "identity_verifier": False, "retrieval_configured": False}
        for key in ready.keys() - {"ok"}:
            altered = {**ready, key: True}
            client = Mock(deadline=time.monotonic() + 15)
            client.request.return_value = (200, altered)
            with self.assertRaises(probe.ProbeFailure):
                probe.wait_ready(client, Mock(poll=Mock(return_value=None)))

    def test_failure_stops_process_and_removes_config_with_clean_environment(self):
        process = Mock(poll=Mock(return_value=None))
        process.wait.side_effect = [subprocess.TimeoutExpired("probe", 2), 0]
        poison = {"THALAMUS_RBX_API": "on", "THALAMUS_DATABASE_URL": "secret",
                  "RBX_TOKEN": "secret", "LITELLM_API_KEY": "secret",
                  "TRACE_EXPORTER_URL": "secret", "https_proxy": "secret"}
        with patch.dict(os.environ, poison), \
                patch.object(probe.subprocess, "Popen", return_value=process) as popen, \
                patch.object(probe, "wait_ready", side_effect=TimeoutError()):
            with self.assertRaises(TimeoutError):
                probe.run(sys.executable)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        invocation = popen.call_args
        self.assertEqual(set(invocation.kwargs["env"]), {"PATH", "HOME", "TMPDIR", "LANG"})
        self.assertFalse(Path(invocation.kwargs["cwd"]).exists())
        self.assertFalse(Path(invocation.args[0][1]).exists())


if __name__ == "__main__":
    unittest.main()
