#!/usr/bin/env python3
"""Read-only checks of a resolved Compose model and staged public certificate.

Does not deploy, contact DNS/ACME, read private keys or change Headscale policy.
Compose input must come from `docker compose ... config --format json`.
"""
import argparse
import json
from pathlib import Path
import subprocess


EXPECTED_PORTS = {
    ("127.0.0.1", 6667, 6667, "tcp"),
    ("127.0.0.1", 6697, 6697, "tcp"),
    ("100.64.0.2", 6697, 6697, "tcp"),
}
GRANT = {"src": ["poco-rbx-trusted@"],
         "dst": ["corbetti-service@"], "ip": ["tcp:6697"]}


def check_model(model):
    service = model["services"]["ergo"]
    if service.get("network_mode") or service.get("privileged"):
        raise ValueError("unexpected_network_mode_or_privilege")
    networks = model.get("networks", {})
    if any(n and n.get("driver") in ("host", "macvlan", "ipvlan")
           for n in networks.values()):
        raise ValueError("unexpected_network_driver")
    ports = service.get("ports", [])
    observed = {(p.get("host_ip"), int(p["published"]), int(p["target"]),
                 p.get("protocol", "tcp")) for p in ports}
    if len(ports) != 3 or observed != EXPECTED_PORTS:
        raise ValueError("unexpected_port_bindings")


def check_policy(before, after):
    # The first pilot adds exactly one grant, preserving every other field and
    # every existing grant. Strict JSON input is intentional: do not strip HuJSON
    # comments with a regex; use a reviewed normalized snapshot instead.
    if not isinstance(before.get("grants"), list):
        raise ValueError("missing_baseline_grants")
    if GRANT in before["grants"]:
        raise ValueError("grant_already_present_rebaseline_required")
    expected = dict(before)
    expected["grants"] = before["grants"] + [GRANT]
    if json.dumps(after, sort_keys=True) != json.dumps(expected, sort_keys=True):
        raise ValueError("policy_delta_not_exact")


def check_certificate(chain, leaf, intermediates, ca_bundle):
    # Separate leaf and intermediates avoids treating an arbitrary self-signed
    # leaf as a trust anchor. The trust bundle must be the OS/provider CA bundle.
    if chain.read_bytes() != leaf.read_bytes() + intermediates.read_bytes():
        raise ValueError("chain_not_leaf_plus_intermediates")
    commands = [
        ["openssl", "verify", "-purpose", "sslserver", "-verify_hostname",
         "irc.rbxsystems.ch", "-CAfile", str(ca_bundle), "-untrusted",
         str(intermediates), str(leaf)],
        ["openssl", "x509", "-in", str(leaf), "-noout", "-checkend", "2592000"],
    ]
    for command in commands:
        result = subprocess.run(command, capture_output=True, timeout=15)
        if result.returncode:
            raise ValueError("certificate_trust_name_or_validity_failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose", type=Path, required=True)
    parser.add_argument("--policy-before", type=Path, required=True)
    parser.add_argument("--policy-after", type=Path, required=True)
    parser.add_argument("--fullchain", type=Path, required=True)
    parser.add_argument("--leaf", type=Path, required=True)
    parser.add_argument("--intermediates", type=Path, required=True)
    parser.add_argument("--ca-bundle", type=Path, required=True)
    args = parser.parse_args()
    try:
        check_model(json.loads(args.compose.read_text()))
        check_policy(json.loads(args.policy_before.read_text()),
                     json.loads(args.policy_after.read_text()))
        check_certificate(args.fullchain, args.leaf, args.intermediates, args.ca_bundle)
    except (ValueError, KeyError, TypeError, OSError, subprocess.TimeoutExpired):
        print("FAIL: staged model, policy or certificate did not pass; no changes applied")
        return 1
    print("PASS: offline checks only; native policy tests, key matching, DNS, live TLS, "
          "SASL and channel authorization remain required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
