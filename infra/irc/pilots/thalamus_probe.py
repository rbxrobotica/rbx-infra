#!/usr/bin/env python3
"""Contract-only, unauthenticated loopback evidence; never production approval.

Requires a trusted thalamus-server binary built without backend features. Starts
one temporary instance, uses memory audit and a Python mock executor, then stops
it. No IRC, provider, /v1/call, configured endpoint, or inherited environment.
Wire contract inspected at thalamus-core 17fccd4. ConfigPolicyPort does not
enforce backend/budget hints or emit AllowWithReview; report that limitation.
"""

import argparse
import copy
import http.client
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import uuid


SCOPE = "contract-only; unauthenticated loopback; nonproduction"
BACKEND = {"id": "synthetic-mock", "backend_type": "Tool"}
BUDGET = {"max_tokens": 32, "max_latency_ms": 1000}
IDENTITY = {"tenant": "synthetic-tenant", "product": "synthetic-project",
            "workflow": "synthetic-pre-post-audit"}
MAX_BODY = 65536


class ProbeFailure(Exception):
    """A contract assertion failed; no response data is printed."""


def require(condition):
    if not condition:
        raise ProbeFailure()


def synthetic_request():
    return {**IDENTITY, "user": "synthetic-actor", "intent": "governance_check",
            "prompt": "Synthetic contract probe. Return synthetic: ok.",
            "requested_backend": dict(BACKEND), "budget_hint": dict(BUDGET)}


def config(port, policy_id):
    return {"listen": f"127.0.0.1:{port}", "policies": [{
        **IDENTITY, "id": policy_id, "permitted_backends": [dict(BACKEND)],
        **BUDGET, "context_grants": [], "redaction_rules": [],
        "audit_required": True, "risk_threshold": "Low",
        "require_run_correlation": False}]}


def isolated_environment(directory):
    # An allowlist built from constants, never a filtered copy of os.environ.
    return {"PATH": "/usr/bin:/bin", "HOME": directory, "TMPDIR": directory,
            "LANG": "C.UTF-8"}


class LocalClient:
    def __init__(self, port, deadline):
        self.port, self.deadline = port, deadline

    def request(self, path, body=None):
        allowed = {"/readyz", "/v1/decide", "/v1/pre-call", "/v1/post-call"}
        if path.startswith("/v1/audit/"):
            require(str(uuid.UUID(path.removeprefix("/v1/audit/"))) == path[10:])
        else:
            require(path in allowed)
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        # HTTPConnection neither uses environment proxies nor follows redirects.
        conn = http.client.HTTPConnection("127.0.0.1", self.port,
                                          timeout=min(2, remaining))
        try:
            conn.request("GET" if body is None else "POST", path,
                         None if body is None else json.dumps(body),
                         {"Content-Type": "application/json"})
            response = conn.getresponse()
            raw = response.read(MAX_BODY + 1)
            require(len(raw) <= MAX_BODY)
            result = json.loads(raw)
            require(isinstance(result, dict))
            return response.status, result
        finally:
            conn.close()


def checked_envelope(pre, request, policy_id):
    require(pre.get("decision") == "Allow")
    require(pre.get("policy_id") == policy_id)
    require(pre.get("review_reason") is None and pre.get("policy_ref") is None)
    require(all(request.get(k) == v for k, v in IDENTITY.items()))
    require(request.get("requested_backend") == BACKEND)
    require(request.get("budget_hint") == BUDGET)
    ids = {key: pre.get(key) for key in ("trace_id", "audit_id")}
    for value in ids.values():
        require(isinstance(value, str))
        parsed = uuid.UUID(value)
        require(str(parsed) == value and parsed.version == 4)
    require(ids["trace_id"] != ids["audit_id"])
    expected = {**ids, "backend_handle_id": BACKEND["id"],
                "prompt": request["prompt"], "policy_ref": policy_id,
                "budget_max_tokens": BUDGET["max_tokens"],
                "budget_max_latency_ms": BUDGET["max_latency_ms"]}
    require(pre.get("envelope") == expected)
    return expected


class MockExecutor:
    def __init__(self):
        self.calls = 0

    def execute(self, pre, request, policy_id):
        envelope = checked_envelope(pre, request, policy_id)
        self.calls += 1
        return {"audit_id": envelope["audit_id"], "content": "synthetic: ok",
                "tokens_used": 1, "latency_ms": 1}


def expect_refusal(executor, pre, request, policy_id):
    before = executor.calls
    try:
        executor.execute(pre, request, policy_id)
    except (ProbeFailure, ValueError, TypeError, KeyError):
        require(executor.calls == before)
        return
    raise ProbeFailure()


def exercise(client, policy_id):
    request, executor = synthetic_request(), MockExecutor()
    status, decision = client.request("/v1/decide", request)
    require(status == 200 and decision.get("decision") == "Allow")
    require(decision.get("policy_id") == policy_id)
    observations = {}
    for case in ("unknown_tenant", "unknown_backend", "excessive_budget"):
        invalid = copy.deepcopy(request)
        if case == "unknown_tenant":
            invalid["tenant"] = "synthetic-unmatched"
        elif case == "unknown_backend":
            invalid["requested_backend"]["id"] = "synthetic-unpermitted"
        else:
            invalid["budget_hint"]["max_tokens"] += 1
        status, decision = client.request("/v1/decide", invalid)
        require(status == 200 and decision.get("decision") in {"Allow", "Deny"})
        if case == "unknown_tenant":
            require(decision["decision"] == "Deny")
        status, pre = client.request("/v1/pre-call", invalid)
        require(status == 200 and pre.get("decision") in {"Allow", "Deny"})
        if case == "unknown_tenant":
            require(pre["decision"] == "Deny" and pre.get("envelope") is None)
        expect_refusal(executor, pre, invalid, policy_id)
        observations[case] = {"server_decide": decision["decision"],
                              "server_pre_call": pre["decision"],
                              "mock_executor": "refused"}
    require(executor.calls == 0)
    status, pre = client.request("/v1/pre-call", request)
    require(status == 200)
    result = executor.execute(pre, request, policy_id)
    status, post = client.request("/v1/post-call", result)
    expected_post = {"status": "Valid", "risk_class": "Low",
                     "executable_by_agent": True, "schema_valid": True,
                     "audit_id": pre["audit_id"]}
    require(status == 200 and post == expected_post)
    status, audit = client.request("/v1/audit/" + pre["audit_id"])
    require(status == 200 and audit.get("audit_id") == pre["audit_id"])
    events = audit.get("events", [])
    require([e.get("kind") for e in events] == ["PreCallDecision", "PostCallOutcome"])
    require(all(e.get("trace_id") == pre["trace_id"] for e in events))
    require(events[0].get("details") == {
        **IDENTITY, "user": request["user"], "policy_ref": policy_id,
        "decision": "Allow", "backend": BACKEND["id"]})
    require(events[1].get("details") == {
        k: v for k, v in expected_post.items() if k != "audit_id"})
    orphan = {**result, "audit_id": str(uuid.uuid4())}
    status, error = client.request("/v1/post-call", orphan)
    require(status == 404 and error.get("code") == "UNKNOWN_AUDIT_ID")
    require(executor.calls == 1)
    return {"result": "passed", "scope": SCOPE, "negative_cases": observations,
            "mock_executions": executor.calls, "post_call": "Valid",
            "audit_events": 2, "correlation": "verified", "orphan_post": "refused",
            "review_unknown_decisions": "client unit tests only",
            "server_allows_invalid_requests": [case for case, observed in observations.items()
                                               if observed["server_decide"] == "Allow"]}


def wait_ready(client, process):
    while time.monotonic() < client.deadline:
        require(process.poll() is None)
        try:
            status, ready = client.request("/readyz")
        except (OSError, http.client.HTTPException):
            time.sleep(0.05)
            continue
        require(status == 200 and ready.get("ok") is True)
        for key in ("backend_configured", "durable_audit", "identity_verifier",
                    "retrieval_configured"):
            require(ready.get(key) is False)
        require(process.poll() is None)
        return
    raise TimeoutError()


def run(binary):
    binary = str(Path(binary).resolve(strict=True))
    with tempfile.TemporaryDirectory(prefix="thalamus-contract-") as directory:
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        policy_id = "synthetic-" + str(uuid.uuid4())
        config_path = Path(directory) / "policy.json"
        config_path.write_text(json.dumps(config(port, policy_id)), encoding="utf-8")
        process = subprocess.Popen([binary, str(config_path)], cwd=directory,
                                   env=isolated_environment(directory),
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, close_fds=True)
        try:
            client = LocalClient(port, time.monotonic() + 15)
            wait_ready(client, process)
            summary = exercise(client, policy_id)
            summary["readiness"] = "no backend, durable audit, identity, or retrieval"
            return summary
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-binary", required=True, type=Path)
    args = parser.parse_args()
    try:
        summary = run(args.server_binary)
    except Exception as error:
        print(json.dumps({"result": "failed", "scope": SCOPE,
                          "error": type(error).__name__}))
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
