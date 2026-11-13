#!/usr/bin/env python3
"""Bounded publication of the recovered lab snapshot, with mandatory origin cleanup."""

import argparse
import hashlib
import importlib.util
import io
import json
import pathlib
import subprocess
import tarfile
import time

HOST = "root@161.97.147.76"
ROOT = "/var/cache/rbx-dns-origin-source"
NSD_SHA256 = "44e1d576f03084965763c4979942ba158dc8e38b00adedd67ed3739d02da44a2"
TOOL_FILES = ["usr/sbin/nsd", "usr/sbin/nsd-checkconf", "usr/sbin/nsd-checkzone",
              "usr/lib/x86_64-linux-gnu/libevent-2.1.so.7",
              "usr/lib/x86_64-linux-gnu/libfstrm.so.0",
              "usr/lib/x86_64-linux-gnu/libhiredis.so.1",
              "usr/lib/x86_64-linux-gnu/libhiredis_ssl.so.1",
              "usr/lib/x86_64-linux-gnu/libprotobuf-c.so.1"]
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
        origin = pathlib.Path(__file__).parent.parent / "dns-origin"
        files = {"tools/" + name: (args.tools_root / name).read_bytes() for name in TOOL_FILES}
        for path, name in [(origin / "remote-origin.py", "remote-origin.py"),
                           (origin / "nsd-origin.conf.in", "nsd-origin.conf.in"),
                           (fixture / "origin-manifest.json", "manifest.json"),
                           (fixture / "seed/reference.zone", "phase-1.zone"),
                           (fixture / "recovered/reference.zone", "phase-2.zone")]:
            files[name] = path.read_bytes()
        bundle = args.work_dir / "public-origin-bundle.tar.gz"
        with tarfile.open(bundle, "w:gz") as archive:
            for name, data in files.items():
                member = tarfile.TarInfo(name)
                member.size, member.mode = len(data), 0o755 if name.startswith("tools/usr/sbin/") else 0o644
                archive.addfile(member, io.BytesIO(data))
        report["public_bundle_sha256"] = hashlib.sha256(bundle.read_bytes()).hexdigest()
        report["public_bundle_bytes"] = bundle.stat().st_size
        remote("mkdir -m 0755 " + ROOT)
        staged = True
        subprocess.run(scp + [str(bundle), HOST + ":" + ROOT + "/bundle.tar.gz"],
                       capture_output=True, check=True, timeout=90)
        extract = '''import hashlib,io,pathlib,tarfile
root=pathlib.Path("/var/cache/rbx-dns-origin-source")
raw=(root/"bundle.tar.gz").read_bytes()
assert len(raw)<4*1024*1024 and hashlib.sha256(raw).hexdigest()==EXPECTED
members={}
with tarfile.open(fileobj=io.BytesIO(raw),mode="r:gz") as archive:
 for entry in archive:
  assert entry.name in ALLOWED and entry.name not in members and entry.isfile()
  assert 0<=entry.size<=2*1024*1024
  members[entry.name]=(archive.extractfile(entry).read(),entry.mode)
assert set(members)==set(ALLOWED) and sum(len(v[0]) for v in members.values())<4*1024*1024
for name,(data,mode) in members.items():
 path=root/name
 path.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
 path.write_bytes(data)
 path.chmod(mode)
(root/"bundle.tar.gz").unlink()
'''
        remote("python3 -", "EXPECTED=" + repr(report["public_bundle_sha256"]) +
               "\nALLOWED=" + repr(list(files)) + "\n" + extract)
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
            cleanup_command = "if test -f " + ROOT + "/remote-origin.py; then python3 " + ROOT + "/remote-origin.py cleanup; else python3 -; fi"
            fallback = '''import pathlib,shutil,subprocess
p=pathlib.Path("/var/cache/rbx-dns-origin-source")
assert p.is_dir() and not p.is_symlink()
assert not pathlib.Path("/run/rbx-dns-origin-control").exists()
assert subprocess.run(["iptables","-S","RBX_DNS_PILOT"],capture_output=True).returncode!=0
assert subprocess.run(["systemctl","is-active","--quiet","rbx-dns-origin.service"]).returncode!=0
shutil.rmtree(p)
'''
            cleanup = remote(cleanup_command, fallback, check=False)
            report["cleanup_exit_code"] = cleanup.returncode
            report["cleanup"] = cleanup.stdout
            report["after"] = json.loads(remote("python3 -", PREFLIGHT).stdout)
        (args.work_dir / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")
    if report.get("cleanup_exit_code") != 0 or not all(
            report["after"][k] for k in ["public_port_53_free", "staging_absent", "control_absent",
                                       "owned_chain_absent", "origin_unit_inactive", "cleanup_timer_inactive"]):
        raise RuntimeError("Owned origin cleanup did not complete")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
