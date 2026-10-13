#!/usr/bin/env python3
"""Generate only synthetic inputs; exercise the real offline bundle preparer."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import jinja2
import yaml

repo, scratch = map(Path, sys.argv[1:])
fixtures = repo / "scripts/tests/fixtures/headscale"
staged = scratch / "fixtures"
staged.mkdir(mode=0o700)
for name in ("native-runner.sh", "bootstrap-deny-all.json", "bootstrap-derp.yaml"):
    shutil.copyfile(fixtures / name, staged / name)

role = repo / "bootstrap/ansible/roles/headscale_candidate"
variables = yaml.safe_load((role / "defaults/main.yml").read_text())
variables.update(
    headscale_domain="control.example.invalid",
    headscale_state_dir="/var/lib/headscale",
    headscale_policy_path="/etc/headscale/policy.hujson",
)
rendered = jinja2.Environment(
    undefined=jinja2.StrictUndefined, keep_trailing_newline=True
).from_string((role / "templates/config.yaml.j2").read_text()).render(**variables)
config = yaml.safe_load(rendered)
current = scratch / "current.yaml"
candidate = scratch / "candidate.yaml"
policy_path = scratch / "preserved-policy.json"
# Deliberately different formatting: preparation must preserve current bytes.
current.write_text(yaml.safe_dump(config, sort_keys=False))
candidate.write_text(rendered)
shutil.copyfile(fixtures / "preserved-policy.json", policy_path)
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(policy, output):
    command = [
        sys.executable, "-I", "-B", str(repo / "scripts/network/headscale_candidate.py"),
        "--current-config", str(current), "--candidate-config", str(candidate),
        "--policy", str(policy), "--expect-config-sha256", sha(current),
        "--expect-policy-sha256", sha(policy) if policy.exists() else "0" * 64,
        "--policy-reference-path", "/etc/headscale/policy.hujson",
        "--output-dir", str(output),
    ]
    result = subprocess.run(command, capture_output=True, check=False, timeout=15)
    return result, json.loads(result.stdout)


result, receipt = prepare(policy_path, scratch / "bundle")
assert result.returncode == 0 and receipt["status"] == "prepared"
assert (scratch / "bundle/config.yaml").read_bytes() == current.read_bytes()
assert (scratch / "bundle/policy.hujson").read_bytes() == policy_path.read_bytes()
assert receipt["hashes"]["bundled_config_sha256"] == sha(current)
assert receipt["hashes"]["bundled_policy_sha256"] == sha(policy_path)

# These cases must stop BEFORE native Headscale is ever invoked. In particular,
# absent rules can mean allow-all, while empty grants would erase permissions.
negative_inputs = {"missing": None, "empty-file": b"", "no-rules": b"{}\n",
                   "empty-grants": b'{"grants": []}\n'}
for name, content in negative_inputs.items():
    bad_policy = scratch / (name + ".json")
    if content is not None:
        bad_policy.write_bytes(content)
    output = scratch / (name + "-bundle")
    bad_result, bad_receipt = prepare(bad_policy, output)
    assert bad_result.returncode != 0 and bad_receipt["status"] == "blocked"
    assert not output.exists(), "rejected input unexpectedly created a bundle"

bootstrap = copy.deepcopy(config)
bootstrap.update(
    server_url="http://127.0.0.1:18000", listen_addr="127.0.0.1:18000",
    metrics_listen_addr="127.0.0.1:19090", grpc_listen_addr="127.0.0.1:15043",
    tls_letsencrypt_hostname="",
)
bootstrap["derp"].update(urls=[], paths=["/fixtures/bootstrap-derp.yaml"], auto_update_enabled=False)
bootstrap["policy"]["path"] = "/fixtures/bootstrap-deny-all.json"
(staged / "bootstrap.yaml").write_text(yaml.safe_dump(bootstrap, sort_keys=False))
policy = json.loads(policy_path.read_text())
assert len(policy["tests"]) == 2 and len(policy["grants"]) == 2
assert {test["src"] for test in policy["tests"]} == {"admin@", "phone@"}
assert all(test.get("proto") == "tcp" for test in policy["tests"])
assert all(len(test["accept"]) == 1 and len(test["deny"]) == 3 for test in policy["tests"])
invalid = copy.deepcopy(policy)
invalid["grants"] = "invalid-schema"
(staged / "invalid-schema.json").write_text(json.dumps(invalid))
missing = copy.deepcopy(policy)
missing["tests"][0]["src"] = "missing-identity@"
(staged / "missing-identity.json").write_text(json.dumps(missing))
reversed_ports = copy.deepcopy(policy)
reversed_ports["grants"][0]["ip"], reversed_ports["grants"][1]["ip"] = (
    reversed_ports["grants"][1]["ip"], reversed_ports["grants"][0]["ip"]
)
(staged / "reversed-ports.json").write_text(json.dumps(reversed_ports))

report = {
    "schema_version": "headscale-synthetic-native-test-v1",
    "headscale_version": "0.29.3",
    "synthetic_only": True,
    "preparer_rejections": list(negative_inputs),
    "hashes": receipt["hashes"],
    "executed_config_sha256": sha(scratch / "bundle/config.yaml"),
    "executed_policy_sha256": sha(scratch / "bundle/policy.hujson"),
    "runtime_field_remappings": [],
    "synthetic_users": 2,
    "synthetic_nodes": 3,
    "allow_assertions": 2,
    "deny_assertions": 6,
    "bootstrap_is_candidate_verdict": False,
    "live_traffic_verified": False,
    "deployment_authorized": False,
}
(scratch / "test-report.json").write_text(json.dumps(report, sort_keys=True))
