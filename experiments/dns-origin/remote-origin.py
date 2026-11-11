#!/usr/bin/env python3
"""Task-scoped transient NSD lifecycle; run only for the authorized synthetic lab."""

import argparse
import hashlib
import ipaddress
import json
import os
import pathlib
import shutil
import subprocess
import time

ROOT = pathlib.Path("/run/rbx-dns-origin-source")
CONTROL = pathlib.Path("/run/rbx-dns-origin-control")
UNIT = "rbx-dns-origin.service"
TIMER = "rbx-dns-origin-cleanup"
CHAIN = "RBX_DNS_PILOT"
TAG = "rbx-dns-origin-20261111"
ADDRESS = "161.97.147.76"
PARTNERS = ["109.201.133.251", "178.156.179.118", "51.91.57.244",
            "185.206.180.55", "45.83.248.12", "45.83.249.12",
            "45.83.250.12", "45.83.251.12", "185.206.180.204"]


def run(args, check=True, env=None):
    return subprocess.run(args, check=check, capture_output=True, text=True,
                          timeout=30, env=env)


def iptables(*args, check=True):
    return run(["/usr/sbin/iptables", "-w", "5", *args], check=check)


def jump(protocol):
    return ["INPUT", "-d", ADDRESS, "-p", protocol, "--dport", "53",
            "-m", "comment", "--comment", TAG, "-j", CHAIN]


def prepare():
    assert os.geteuid() == 0
    assert ROOT.is_dir() and not ROOT.is_symlink()
    if CONTROL.exists():
        raise RuntimeError("Task control state already exists; inspect instead of replacing")
    # Recheck public TCP/UDP 53 ownership immediately before mutation.
    listeners = run(["ss", "-H", "-lnut", "sport = :53"]).stdout
    if any(ADDRESS + ":53" in line or "0.0.0.0:53" in line or "*:53" in line
           for line in listeners.splitlines()):
        raise RuntimeError("Public DNS port is occupied")
    client = str(ipaddress.IPv4Address(os.environ["SSH_CONNECTION"].split()[0]))
    if iptables("-S", CHAIN, check=False).returncode == 0:
        raise RuntimeError("Firewall chain already exists")
    CONTROL.mkdir(mode=0o700)
    (CONTROL / "baseline.rules").write_text(run(["/usr/sbin/iptables-save"]).stdout)
    meta = {"client": client, "partners": PARTNERS, "tag": TAG,
            "address": ADDRESS, "prepared_at_epoch": time.time()}
    (CONTROL / "state.json").write_text(json.dumps(meta, indent=2) + "\n")
    os.chmod(CONTROL / "state.json", 0o600)
    os.chmod(CONTROL / "baseline.rules", 0o600)
    config = (ROOT / "nsd-origin.conf.in").read_text()
    acl = "\n".join(f"    provide-xfr: {ip} NOKEY\n    notify: {ip} NOKEY"
                    for ip in PARTNERS)
    # Same-host positive transfer control; the external tester is query-only.
    acl += f"\n    provide-xfr: {ADDRESS} NOKEY"
    (ROOT / "nsd.conf").write_text(config.replace("<provider-zone-transfer-acl>", acl))
    env = dict(os.environ, LD_LIBRARY_PATH=str(ROOT / "tools/usr/lib/x86_64-linux-gnu"))
    run([str(ROOT / "tools/usr/sbin/nsd-checkconf"), str(ROOT / "nsd.conf")], env=env)
    for phase in ("phase-1", "phase-2"):
        run([str(ROOT / "tools/usr/sbin/nsd-checkzone"),
             "cloudns-xfr-pilot.rbxsystems.ch.", str(ROOT / (phase + ".zone"))], env=env)
    # Safety timer removes only this task's process, chain and public staging.
    run(["systemd-run", "--unit=" + TIMER, "--on-active=30m", "--collect",
         "/usr/bin/python3", str(ROOT / "remote-origin.py"), "cleanup"])
    iptables("-N", CHAIN)
    try:
        for ip in PARTNERS + [client]:
            iptables("-A", CHAIN, "-s", ip, "-m", "comment", "--comment", TAG, "-j", "ACCEPT")
        iptables("-A", CHAIN, "-j", "RETURN")
        for protocol in ("tcp", "udp"):
            iptables("-I", *jump(protocol)[:1], "1", *jump(protocol)[1:])
    except Exception:
        cleanup(remove_source=False)
        raise
    print(json.dumps({"prepared": True, "partner_ipv4_count": len(PARTNERS),
                      "test_client_count": 1, "persistent_ufw_change": False}))


def start(phase):
    assert CONTROL.is_dir()
    if run(["systemctl", "is-active", "--quiet", UNIT], check=False).returncode == 0:
        raise RuntimeError("Task origin is already active")
    select_phase(phase, reload=False)
    meta = json.loads((CONTROL / "state.json").read_text())
    allow = PARTNERS + [meta["client"], ADDRESS, "127.0.0.0/8"]
    properties = [
        "DynamicUser=yes", "User=rbx-dns-pilot", "NoNewPrivileges=yes",
        "CapabilityBoundingSet=CAP_NET_BIND_SERVICE", "AmbientCapabilities=CAP_NET_BIND_SERVICE",
        "ProtectSystem=strict", "ProtectHome=yes", "PrivateTmp=yes", "PrivateDevices=yes",
        "ProtectKernelTunables=yes", "ProtectKernelModules=yes", "ProtectControlGroups=yes",
        "RestrictSUIDSGID=yes", "LockPersonality=yes", "MemoryDenyWriteExecute=yes",
        "RestrictAddressFamilies=AF_INET AF_UNIX", "IPAddressDeny=any",
        "InaccessiblePaths=/var/lib/postgresql /var/lib/rancher /etc/ssh",
        "RuntimeDirectory=rbx-dns-origin-state", "RuntimeDirectoryMode=0700",
        "ReadWritePaths=/run/rbx-dns-origin-state", "WorkingDirectory=/run/rbx-dns-origin-state",
        "MemoryMax=128M", "CPUQuota=10%", "TasksMax=16", "LimitNOFILE=128",
        "RuntimeMaxSec=1200", "UMask=0077",
        "Environment=LD_LIBRARY_PATH=" + str(ROOT / "tools/usr/lib/x86_64-linux-gnu"),
        "IPAddressAllow=" + " ".join(allow),
    ]
    args = ["systemd-run", "--unit=rbx-dns-origin", "--collect", "--service-type=exec"]
    for value in properties:
        args.extend(["--property", value])
    args += [str(ROOT / "tools/usr/sbin/nsd"), "-d", "-c", str(ROOT / "nsd.conf")]
    result = run(args)
    print(result.stdout.strip() or result.stderr.strip())
    print(run(["systemctl", "show", UNIT, "-p", "ActiveState", "-p", "SubState",
               "-p", "DynamicUser", "-p", "MemoryMax", "-p", "RuntimeMaxUSec"]).stdout.strip())


def select_phase(phase, reload=True):
    source = ROOT / (phase + ".zone")
    data = source.read_bytes()
    manifest = json.loads((ROOT / "manifest.json").read_text())
    assert hashlib.sha256(data).hexdigest() == manifest["phases"][phase]["sha256"]
    temp = ROOT / "origin.zone.new"
    temp.write_bytes(data)
    temp.chmod(0o644)
    temp.replace(ROOT / "origin.zone")
    if reload:
        run(["systemctl", "kill", "--signal=HUP", "--kill-whom=main", UNIT])
    print(json.dumps({"selected_phase": phase, "public_zone_sha256": hashlib.sha256(data).hexdigest()}))


def stop():
    run(["systemctl", "stop", UNIT], check=False)
    print(json.dumps({"origin_active": run(["systemctl", "is-active", "--quiet", UNIT], check=False).returncode == 0}))


def cleanup(remove_source=True):
    assert os.geteuid() == 0
    stop()
    for protocol in ("tcp", "udp"):
        if iptables("-C", *jump(protocol), check=False).returncode == 0:
            iptables("-D", *jump(protocol))
    if iptables("-S", CHAIN, check=False).returncode == 0:
        rules = iptables("-S", CHAIN).stdout
        if any(TAG not in line and "-j RETURN" not in line and line != "-N " + CHAIN
               for line in rules.splitlines()):
            raise RuntimeError("Unexpected task-chain rule; inspect before deleting")
        iptables("-F", CHAIN)
        iptables("-X", CHAIN)
    run(["systemctl", "stop", TIMER + ".timer"], check=False)
    if CONTROL.exists():
        shutil.rmtree(CONTROL)
    if remove_source and ROOT.exists():
        shutil.rmtree(ROOT)
    print(json.dumps({"task_firewall_chain_absent": iptables("-S", CHAIN, check=False).returncode != 0,
                      "origin_process_stopped": True, "source_removed": remove_source}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "start", "phase-2", "stop", "cleanup"])
    parser.add_argument("--phase", choices=["phase-1", "phase-2"], default="phase-1")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare()
    elif args.action == "start":
        start(args.phase)
    elif args.action == "phase-2":
        select_phase("phase-2")
    elif args.action == "stop":
        stop()
    else:
        cleanup()
