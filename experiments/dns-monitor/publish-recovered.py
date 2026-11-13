#!/usr/bin/env python3
"""Bounded publication of the recovered lab snapshot, with mandatory origin cleanup."""

import argparse
import hashlib
import importlib.util
import json
import pathlib
import subprocess
import time

HOST = "root@161.97.147.76"
ROOT = "/var/cache/rbx-dns-origin-source"
NSD_SHA256 = "44e1d576f03084965763c4979942ba158dc8e38b00adedd67ed3739d02da44a2"
PREFLIGHT = '''import json,pathlib,subprocess
root=pathlib.Path("/var/cache/rbx-dns-origin-source")
control=pathlib.Path("/run/rbx-dns-origin-control")
listeners=subprocess.check_output(["ss","-H","-lnut","sport = :53"],text=True)
occupied=any("161.97.147.76:53" in r or "0.0.0.0:53" in r or "*:53" in r for r in listeners.splitlines())
chain=subprocess.run(["iptables","-w","5","-S","RBX_DNS_PILOT"],capture_output=True).returncode==0
services=subprocess.check_output(["systemctl","is-active","postgresql","k3s-agent"],text=True).splitlines()
origin_idle=subprocess.run(["systemctl","is-active","--quiet","rbx-dns-origin.service"]).returncode!=0
timer_idle=subprocess.run(["systemctl","is-active","--quiet","rbx-dns-origin-cleanup.timer"]).returncode!=0
print(json.dumps({"public_port_53_free":not occupied,"staging_absent":not root.exists(),"control_absent":not control.exists(),"owned_chain_absent":not chain,"origin_unit_inactive":origin_idle,"cleanup_timer_inactive":timer_idle,"postgresql_k3s_states":services}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["ssh-agent", "tools-root", "resilience-source", "work-dir"]:
        parser.add_argument("--" + name, required=True, type=pathlib.Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    args.work_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
           "-o", "ConnectTimeout=10", "-o", "IdentityAgent=" + str(args.ssh_agent)]
    scp = ["scp", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
           "-o", "IdentityAgent=" + str(args.ssh_agent)]

    def remote(command, input_text=None, check=True):
        return subprocess.run(ssh + [HOST, command], input=input_text,
                              capture_output=True, text=True, check=check, timeout=45)

    before = json.loads(remote("python3 -", PREFLIGHT).stdout)
    if not all(before[k] for k in ["public_port_53_free", "staging_absent",
                                   "control_absent", "owned_chain_absent",
                                   "origin_unit_inactive", "cleanup_timer_inactive"]):
        raise RuntimeError("Origin slot is not clean")
    if before["postgresql_k3s_states"] != ["active", "active"]:
        raise RuntimeError("Shared host baseline differs")
    report = {"scope": "existing undelegated synthetic zone only", "before": before,
              "execute_requested": args.execute, "private_signer_copied_to_host": False}
    if not args.execute:
        (args.work_dir / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report))
        return
    if hashlib.sha256((args.tools_root / "usr/sbin/nsd").read_bytes()).hexdigest() != NSD_SHA256:
        raise ValueError("Unreviewed authoritative binary")
    fixture = args.resilience_source / "fixtures/dns/signer-recovery-20261113"
    manifest = json.loads((fixture / "origin-manifest.json").read_text())
    for phase, directory in [("phase-1", "seed"), ("phase-2", "recovered")]:
        if hashlib.sha256((fixture / directory / "reference.zone").read_bytes()).hexdigest() != manifest["phases"][phase]["sha256"]:
            raise ValueError("Public fixture digest differs")
    staged = False
    try:
        remote("mkdir -m 0755 " + ROOT + " && mkdir -m 0755 " + ROOT + "/tools")
        staged = True
        subprocess.run(scp + ["-pr", str(args.tools_root / "usr"), HOST + ":" + ROOT + "/tools/"],
                       capture_output=True, check=True, timeout=90)
        origin = pathlib.Path(__file__).parent.parent / "dns-origin"
        for path, name in [(origin / "remote-origin.py", "remote-origin.py"),
                           (origin / "nsd-origin.conf.in", "nsd-origin.conf.in"),
                           (fixture / "origin-manifest.json", "manifest.json"),
                           (fixture / "seed/reference.zone", "phase-1.zone"),
                           (fixture / "recovered/reference.zone", "phase-2.zone")]:
            subprocess.run(scp + [str(path), HOST + ":" + ROOT + "/" + name],
                           capture_output=True, check=True, timeout=30)
        remote("chmod -R a+rX " + ROOT)
        report["prepare"] = remote("python3 " + ROOT + "/remote-origin.py prepare").stdout
        started = time.monotonic()
        report["start"] = remote("python3 " + ROOT + "/remote-origin.py start --phase phase-2").stdout
        spec = importlib.util.spec_from_file_location("health", args.resilience_source / "scripts/check-transfer-pilot-health.py")
        health = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(health)
        publication = json.loads((fixture / "recovered/publication.json").read_text())
        reference = health.load_reference(fixture / "recovered/reference.zone",
                                          publication["sha256"], publication["ds_sha256"])
        deadline = time.monotonic() + 300
        while True:
            check = health.run(reference, 86400, publication["sha256"], publication["ds_sha256"])
            if check["exit_code"] == 0:
                break
            if time.monotonic() > deadline:
                raise RuntimeError("Managed publication did not converge")
            time.sleep(2)
        (args.work_dir / "managed-health.json").write_text(json.dumps(check, indent=2) + "\n")
        report["managed_24_checks_healthy"] = True
        report["publication_convergence_seconds"] = round(time.monotonic() - started, 3)
        adapter = args.work_dir / "public-fixture-adapter"
        adapter.mkdir()
        for src, name in [(fixture / "recovered/reference.zone", "phase-2.zone"),
                          (fixture / "seed/trust-anchor.key", "trust-anchor.key"),
                          (fixture / "recovered/publication.json", "manifest.json")]:
            (adapter / name).write_bytes(src.read_bytes())
        probe = subprocess.run(["python3", "-B", str(origin / "probe-managed-secondary.py"),
                                "--fixtures", str(adapter), "--tools-root", str(args.tools_root),
                                "--work-dir", str(args.work_dir / "managed-full-validation"),
                                "--expected-canary", "signer-after-restore"],
                               capture_output=True, text=True, timeout=240)
        report["full_managed_probe"] = json.loads(probe.stdout)
        if probe.returncode != 0:
            raise RuntimeError("Managed current-signature validation failed")
    finally:
        if staged:
            cleanup = remote("python3 " + ROOT + "/remote-origin.py cleanup", check=False)
            report["cleanup_exit_code"] = cleanup.returncode
            report["cleanup"] = cleanup.stdout
            report["after"] = json.loads(remote("python3 -", PREFLIGHT).stdout)
        (args.work_dir / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
