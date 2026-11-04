"""Offline validation of the dedicated research collector's deployment bounds."""

import json
from pathlib import Path
import re
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]
overlay = ROOT / "apps/prod/personal-research"
rendered = subprocess.check_output(["kubectl", "kustomize", str(overlay)], text=True)
objects = list(yaml.safe_load_all(rendered))
cron = next(o for o in objects if o["kind"] == "CronJob")
spec = cron["spec"]
assert spec["timeZone"] == "America/Sao_Paulo" and spec["schedule"] == "30 8 * * 1,4"
assert spec["concurrencyPolicy"] == "Forbid"
job = spec["jobTemplate"]["spec"]
assert job["activeDeadlineSeconds"] <= 600 and job["backoffLimit"] <= 1
pod = job["template"]["spec"]
assert pod["automountServiceAccountToken"] is False
assert pod["securityContext"]["runAsNonRoot"] is True
container = pod["containers"][0]
assert container["args"] == ["--fetch", "--output", "/work"]
assert container["securityContext"]["readOnlyRootFilesystem"] is True
assert container["env"][1]["valueFrom"]["fieldRef"]["fieldPath"] == "metadata.labels['batch.kubernetes.io/job-name']"
assert not any("migration" in json.dumps(o).lower() for o in objects)
role = next(o for o in objects if o["kind"] == "Role")
assert role["metadata"]["namespace"] == "rbx-ia-br"
assert role["rules"] == [{"apiGroups": [""], "resources": ["secrets"], "resourceNames": ["personal-research-runtime", "personal-research-registry"], "verbs": ["get"]}]
app = yaml.safe_load((ROOT / "gitops/app-of-apps/personal-research.yml").read_text())
assert "automated" not in app["spec"]["syncPolicy"]
if not spec["suspend"]:
    assert re.fullmatch(r"ghcr.io/rbxrobotica/personal-research(?::sha-[0-9a-f]{7,40})?@sha256:[0-9a-f]{64}", container["image"]), "activation requires a published digest"
print(json.dumps({"status": "PASS", "resources": len(objects), "suspended": spec["suspend"], "automatic_sync": False, "live_state_checked": False}))
