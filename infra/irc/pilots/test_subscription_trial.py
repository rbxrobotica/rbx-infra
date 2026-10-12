"""Provider-free coverage of the supervised job's policy/executor boundary."""

import copy
import unittest
from unittest.mock import Mock, patch
import uuid

from codex_documentary import DocumentaryResult
import subscription_trial as trial
from thalamus_probe import ProbeFailure


PROMPT = "Synthetic documentary question; no external action."
POLICY = "synthetic-trial-policy"
MODEL = "synthetic-model"
USAGE = {"input_tokens": 100, "cached_input_tokens": 30, "output_tokens": 20}


class FakeClient:
    def __init__(self):
        self.calls = []
        self.ids = {key: str(uuid.uuid4()) for key in ("trace_id", "audit_id")}
        self.pre = {**self.ids, "decision": "Allow", "policy_id": POLICY,
                    "review_reason": None, "policy_ref": None,
                    "envelope": {**self.ids, "backend_handle_id": trial.BACKEND["id"],
                                 "prompt": PROMPT, "policy_ref": POLICY,
                                 "budget_max_tokens": trial.BUDGET["max_tokens"],
                                 "budget_max_latency_ms": trial.BUDGET["max_latency_ms"]}}
        self.post = {"status": "Valid", "risk_class": "Low",
                     "executable_by_agent": True, "schema_valid": True,
                     "audit_id": self.ids["audit_id"]}
        self.audit = {"audit_id": self.ids["audit_id"], "events": [
            {"kind": "PreCallDecision", "trace_id": self.ids["trace_id"],
             "details": {**trial.IDENTITY, "user": "supervised-local-operator",
                         "policy_ref": POLICY, "decision": "Allow",
                         "backend": trial.BACKEND["id"]}},
            {"kind": "PostCallOutcome", "trace_id": self.ids["trace_id"],
             "details": {k: v for k, v in self.post.items() if k != "audit_id"}}]}

    def request(self, path, body=None):
        self.calls.append((path, copy.deepcopy(body)))
        if path == "/v1/pre-call":
            return 200, copy.deepcopy(self.pre)
        if path == "/v1/post-call":
            return 200, copy.deepcopy(self.post)
        if path == "/v1/audit/" + self.ids["audit_id"]:
            return 200, copy.deepcopy(self.audit)
        raise AssertionError("unexpected route")


def successful_executor():
    return Mock(return_value=DocumentaryResult(
        status="ok", answer="Synthetic documentary answer.", usage=dict(USAGE),
        elapsed_ms=50, exit_code=0, executor_version="synthetic-test-version"))


class SubscriptionTrialTests(unittest.TestCase):
    def test_deny_review_and_unknown_decisions_never_execute(self):
        for decision in ("Deny", "AllowWithReview", "unknown", "", None):
            with self.subTest(decision=decision):
                client, executor = FakeClient(), successful_executor()
                client.pre["decision"] = decision
                with self.assertRaises(ProbeFailure):
                    trial.consultation(client, POLICY, PROMPT, MODEL, executor)
                executor.assert_not_called()
                self.assertEqual([p for p, _ in client.calls], ["/v1/pre-call"])

    def test_each_changed_envelope_field_never_executes(self):
        for key in FakeClient().pre["envelope"]:
            with self.subTest(key=key):
                client, executor = FakeClient(), successful_executor()
                client.pre["envelope"][key] = "changed"
                with self.assertRaises(ProbeFailure):
                    trial.consultation(client, POLICY, PROMPT, MODEL, executor)
                executor.assert_not_called()

    def test_allow_executes_once_then_posts_and_correlates_audit(self):
        client, executor = FakeClient(), successful_executor()
        report = trial.consultation(client, POLICY, PROMPT, MODEL, executor)
        executor.assert_called_once_with(PROMPT, model=MODEL, live_verified=True,
                                         timeout_s=55, max_output_bytes=262144)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["executor_jobs"], 1)
        self.assertEqual(report["answer"], executor.return_value.answer)
        self.assertEqual(report["usage"], USAGE)
        self.assertEqual(report["tokens_used"], 120)  # Cached input is not added twice.
        self.assertEqual(report["correlation"], "verified")
        self.assertEqual(report["audit_events"], 2)
        self.assertEqual(report["audit_id"], client.ids["audit_id"])
        self.assertEqual(report["trace_id"], client.ids["trace_id"])
        self.assertEqual([p for p, _ in client.calls], [
            "/v1/pre-call", "/v1/post-call", "/v1/audit/" + client.ids["audit_id"]])
        self.assertEqual(client.calls[1][1], {
            "audit_id": client.ids["audit_id"], "content": executor.return_value.answer,
            "tokens_used": 120, "latency_ms": 50})

    def test_executor_failure_has_no_retry_answer_or_post(self):
        client = FakeClient()
        executor = Mock(return_value=DocumentaryResult(
            status="failed", answer="Do not expose partial content", usage=dict(USAGE),
            elapsed_ms=50, error_code="timeout"))
        report = trial.consultation(client, POLICY, PROMPT, MODEL, executor)
        executor.assert_called_once()
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["usage"], USAGE)
        self.assertEqual(report["executor_jobs"], 1)
        self.assertNotIn("answer", report)
        self.assertEqual([p for p, _ in client.calls], ["/v1/pre-call"])
        self.assertIn("no retry", report["quota_consumption"])

    def test_post_or_audit_mismatch_preserves_usage_without_answer_or_retry(self):
        for mismatch in ("post_audit_id", "audit_id", "trace_id", "policy", "outcome"):
            with self.subTest(mismatch=mismatch):
                client, executor = FakeClient(), successful_executor()
                if mismatch == "post_audit_id":
                    client.post["audit_id"] = str(uuid.uuid4())
                elif mismatch == "audit_id":
                    client.audit["audit_id"] = str(uuid.uuid4())
                elif mismatch == "trace_id":
                    client.audit["events"][1]["trace_id"] = str(uuid.uuid4())
                elif mismatch == "policy":
                    client.audit["events"][0]["details"]["policy_ref"] = "other"
                else:
                    client.audit["events"][1]["details"]["status"] = "Invalid"
                report = trial.consultation(client, POLICY, PROMPT, MODEL, executor)
                executor.assert_called_once()
                self.assertEqual(report["status"], "failed")
                self.assertEqual(report["error_code"], "post_validation_failed")
                self.assertEqual(report["usage"], USAGE)
                self.assertNotIn("answer", report)

    def test_usage_overrun_suppresses_answer_even_if_server_says_valid(self):
        client = FakeClient()
        excessive = {**USAGE, "input_tokens": trial.BUDGET["max_tokens"] + 1}
        executor = Mock(return_value=DocumentaryResult(
            status="ok", answer="Withheld over-budget answer", usage=excessive,
            elapsed_ms=50, exit_code=0))
        report = trial.consultation(client, POLICY, PROMPT, MODEL, executor)
        executor.assert_called_once()
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["usage"], excessive)
        self.assertNotIn("answer", report)
        self.assertEqual([p for p, _ in client.calls], ["/v1/pre-call", "/v1/post-call"])

    def test_invalid_prompt_is_rejected_before_pre_call(self):
        for prompt in ("", None, "x" * 8001):
            client, executor = FakeClient(), successful_executor()
            with self.assertRaises(ProbeFailure):
                trial.consultation(client, POLICY, prompt, MODEL, executor)
            executor.assert_not_called()
            self.assertEqual(client.calls, [])

    def test_closed_live_gate_does_not_start_server_or_executor(self):
        with patch.object(trial.subprocess, "Popen") as start, \
                patch.object(trial, "run_codex_documentary") as executor:
            report = trial.run("unused", prompt=PROMPT, model=MODEL)
        self.assertEqual(report["status"], "not_run")
        start.assert_not_called()
        executor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
