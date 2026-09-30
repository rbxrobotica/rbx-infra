#!/usr/bin/env python3
"""Protect Meta callbacks without reopening the Comms console surface."""
from pathlib import Path
import yaml

root = Path(__file__).resolve().parents[1]
docs = list(yaml.safe_load_all((root / "apps/prod/rbx-comms/ingressroute.yml").read_text()))
ingress = next(d for d in docs if d["metadata"]["name"] == "rbx-comms")
assert ingress["spec"]["entryPoints"] == ["websecure"]
routes = ingress["spec"]["routes"]
for host in ("api.comms.rbxsystems.ch", "api.comms.rbx.ia.br"):
    expected = f"Host(`{host}`) && Path(`/api/webhooks/meta/whatsapp`) && (Method(`GET`) || Method(`POST`))"
    matches = [r for r in routes if r["match"] == expected]
    assert len(matches) == 1, f"Missing exact Meta callback for {host}"
    route = matches[0]
    assert not route.get("middlewares"), "Meta uses native token/HMAC verification"
    assert route["services"] == [{"name": "rbx-comms", "port": 80}]
    for provider in ("postmark", "d360"):
        match = f"Host(`{host}`) && PathPrefix(`/api/webhooks/{provider}/`)"
        protected = next(r for r in routes if r["match"] == match)
        assert protected["middlewares"] == [{"name": f"comms-webhook-auth-{provider}", "namespace": "rbx-comms"}]
for route in routes:
    rule = route["match"]
    assert " && Path" in rule, "HTTPS catch-all is forbidden"
    assert "PathPrefix(`/api/contact`)" not in rule
    assert "PathPrefix(`/api/`)" not in rule
    assert "PathPrefix(`/api/webhooks/`)" not in rule
print("Comms exact Meta route and provider isolation: OK")
