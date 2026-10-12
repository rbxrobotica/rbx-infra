"""One supervised, local subscription trial. No CLI, IRC, retry or service.

Call only after a fresh host preflight. This proves job-level pre/post/audit,
not Thalamus mediation of every model request inside the official CLI. The
synthetic loopback policy has no identity verifier or durable audit.
"""

from pathlib import Path
import json
import socket
import subprocess
import tempfile
import time
import uuid

from codex_documentary import run_codex_documentary
from thalamus_probe import LocalClient, isolated_environment, require, wait_ready


IDENTITY = {"tenant": "synthetic-local", "product": "documentary-pilot",
            "workflow": "supervised-subscription-trial"}
BACKEND = {"id": "codex-chatgpt-subscription", "backend_type": "Tool"}
BUDGET = {"max_tokens": 12000, "max_latency_ms": 60000}


def consultation(client, policy_id, prompt, model, executor):
    """Executor injection supports provider-free tests; caller owns preflight."""
    require(isinstance(prompt, str) and 0 < len(prompt.encode("utf-8")) <= 8000)
    request = {**IDENTITY, "user": "supervised-local-operator",
               "intent": "documentary_consultation", "prompt": prompt,
               "requested_backend": BACKEND, "budget_hint": BUDGET}
    status, pre = client.request("/v1/pre-call", request)
    require(status == 200 and pre.get("decision") == "Allow")
    require(pre.get("policy_id") == policy_id and pre.get("review_reason") is None)
    require(pre.get("policy_ref") is None)
    ids = {key: pre.get(key) for key in ("trace_id", "audit_id")}
    for value in ids.values():
        require(isinstance(value, str))
        parsed = uuid.UUID(value)
        require(str(parsed) == value and parsed.version == 4)
    require(ids["trace_id"] != ids["audit_id"])
    require(pre.get("envelope") == {
        **ids, "backend_handle_id": BACKEND["id"], "prompt": prompt,
        "policy_ref": policy_id, "budget_max_tokens": BUDGET["max_tokens"],
        "budget_max_latency_ms": BUDGET["max_latency_ms"]})
    # Exactly one CLI invocation; no retries, fallbacks, or fan-out here.
    result = executor(prompt, model=model, live_verified=True,
                      timeout_s=55, max_output_bytes=262144)
    report = {"status": result.status, "model_requested": model,
              "executor_version": result.executor_version,
              "elapsed_ms": result.elapsed_ms, "error_code": result.error_code,
              "exit_code": result.exit_code,
              "event_shape": result.event_shape,
              "startup_warnings": list(result.startup_warnings),
              "executor_jobs": 1, "usage": result.usage, **ids}
    if result.status != "ok":
        report["quota_consumption"] = "unknown; may have occurred; no retry"
        report["post_call"] = "not submitted: no verified result"
        return report
    try:
        return checked_result(client, report, result, policy_id, request, ids)
    except Exception as error:
        # Keep known usage after a provider call, even if validation/audit fails.
        # Do not return the answer or raw diagnostics on any failed path.
        report.update(status="failed", error_code="post_validation_failed",
                      failure_type=type(error).__name__)
        return report


def checked_result(client, report, result, policy_id, request, ids):
    require(isinstance(result.answer, str) and bool(result.answer.strip()))
    usage = result.usage
    require(isinstance(usage, dict))
    for field in ("input_tokens", "output_tokens", "cached_input_tokens"):
        require(type(usage.get(field)) is int and usage[field] >= 0)
    # Cached input is part of input, not an additional chargeable token count.
    tokens = usage["input_tokens"] + usage["output_tokens"]
    status, post = client.request("/v1/post-call", {
        "audit_id": ids["audit_id"], "content": result.answer,
        "tokens_used": tokens, "latency_ms": result.elapsed_ms})
    report["post_call"] = post.get("status")
    # Measurement after the call cannot undo consumed quota. Suppress the
    # answer when reported usage exceeds the local envelope, regardless of
    # whether the server's post-call validator accepts it.
    require(tokens <= BUDGET["max_tokens"])
    require(0 <= result.elapsed_ms <= BUDGET["max_latency_ms"])
    require(status == 200 and post.get("status") == "Valid")
    require(post.get("audit_id") == ids["audit_id"])
    status, audit = client.request("/v1/audit/" + ids["audit_id"])
    events = audit.get("events", [])
    require(status == 200 and audit.get("audit_id") == ids["audit_id"])
    require([e.get("kind") for e in events] == ["PreCallDecision", "PostCallOutcome"])
    require(all(e.get("trace_id") == ids["trace_id"] for e in events))
    require(events[0].get("details") == {
        **IDENTITY, "user": request["user"], "policy_ref": policy_id,
        "decision": "Allow", "backend": BACKEND["id"]})
    require(events[1].get("details") == {k: v for k, v in post.items() if k != "audit_id"})
    report.update(answer=result.answer, tokens_used=tokens,
                  audit_events=2, correlation="verified")
    return report


def run(server_binary, *, prompt, model, live_verified=False):
    """Trusted local caller asserts preflight; no user message can open this gate."""
    if live_verified is not True:
        return {"status": "not_run", "error_code": "live_gate_not_verified"}
    binary = str(Path(server_binary).resolve(strict=True))
    with tempfile.TemporaryDirectory(prefix="subscription-contract-") as directory:
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        policy_id = "synthetic-subscription-" + str(uuid.uuid4())
        config = {"listen": f"127.0.0.1:{port}", "policies": [{
            **IDENTITY, "id": policy_id, "permitted_backends": [BACKEND],
            **BUDGET, "context_grants": [], "redaction_rules": [],
            "audit_required": True, "risk_threshold": "Low",
            "require_run_correlation": False}]}
        config_path = Path(directory) / "policy.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        process = subprocess.Popen([binary, str(config_path)], cwd=directory,
                                   env=isolated_environment(directory),
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, close_fds=True)
        try:
            client = LocalClient(port, time.monotonic() + 75)
            wait_ready(client, process)
            return consultation(client, policy_id, prompt, model, run_codex_documentary)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
