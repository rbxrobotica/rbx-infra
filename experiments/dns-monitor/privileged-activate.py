#!/usr/bin/env python3
"""Privileged, local-only Ansible activation; only a ping endpoint enters stdin."""

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resilience-source", type=pathlib.Path, required=True)
    parser.add_argument("--reference", type=pathlib.Path, required=True)
    parser.add_argument("--publication", type=pathlib.Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("Root authentication is required for the declared local service")
    config = json.loads(args.publication.read_text())
    if config["origin"] != "cloudns-xfr-pilot.rbxsystems.ch." or not config["synthetic_only"]:
        raise SystemExit("Only the declared synthetic pilot is allowed")
    if hashlib.sha256(args.reference.read_bytes()).hexdigest() != config["sha256"]:
        raise SystemExit("Public reference digest mismatch")
    # Existing foreign unit/files are not silently adopted by an initial install.
    for path in ["/opt/rbx-dns-monitor", "/etc/systemd/system/rbx-dns-pilot-monitor.service",
                 "/etc/systemd/system/rbx-dns-pilot-monitor.timer",
                 "/etc/credstore.encrypted/rbx-dns-monitor-healthchecks"]:
        if pathlib.Path(path).exists():
            raise SystemExit("Existing monitor state requires a reviewed update, not initial activation")
    endpoint = os.read(0, 1024).strip()
    if not re.fullmatch(rb"https://hc-ping\.com/[0-9a-f-]{36}", endpoint):
        raise SystemExit("Invalid per-check endpoint")
    with tempfile.TemporaryDirectory(prefix="rbx-dns-monitor-activation-") as work:
        work = pathlib.Path(work)
        encrypted = subprocess.run(
            ["/usr/bin/systemd-creds", "encrypt", "--with-key=host", "--name=healthchecks", "-", "-"],
            input=endpoint, capture_output=True, check=True, timeout=30).stdout
        credential = work / "healthchecks.encrypted"
        credential.write_bytes(encrypted)
        variables = {"resilience_source": str(args.resilience_source.resolve()),
                     "public_reference": str(args.reference.resolve()),
                     "public_publication": str(args.publication.resolve()),
                     "encrypted_ping_credential": str(credential),
                     "monitor_apply_become": False,
                     "ansible_python_interpreter": "/usr/bin/python3"}
        varfile = work / "public-variables.json"
        varfile.write_text(json.dumps(variables))  # No secret values, only paths.
        command = ["/usr/bin/ansible-playbook", "-i", "localhost,",
                   str(pathlib.Path(__file__).with_name("install.yml")), "-e", "@" + str(varfile)]
        subprocess.run(command + ["--check", "--diff"], check=True, timeout=60)
        subprocess.run(command, check=True, timeout=90)
        subprocess.run(command + ["--check"], check=True, timeout=60)
        # The caller verifies Healthchecks, repeated timer runs and alert delivery.
        subprocess.run(["/usr/bin/systemctl", "start", "rbx-dns-pilot-monitor.service"],
                       check=False, timeout=90)
        print(json.dumps({"declared_local_timer_installed": True,
                          "management_api_key_given_to_service": False,
                          "only_host_wrapped_ping_credential_persisted": True}))


if __name__ == "__main__":
    main()
