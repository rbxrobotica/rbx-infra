#!/usr/bin/env python3
"""Serve restored signed data and validate it with the original public lab anchor."""

import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import pathlib
import signal
import socket
import subprocess
import time

import dns.flags
import dns.message
import dns.query
import dns.rcode

ORIGIN = "cloudns-xfr-pilot.rbxsystems.ch."
NSD_SHA256 = "44e1d576f03084965763c4979942ba158dc8e38b00adedd67ed3739d02da44a2"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["seed", "restored", "tools-root", "resilience-source", "work-dir"]:
        parser.add_argument("--" + name, required=True, type=pathlib.Path)
    args = parser.parse_args()
    args.work_dir.mkdir(mode=0o700, exist_ok=False)
    started = time.monotonic()
    spec = importlib.util.spec_from_file_location(
        "health", args.resilience_source / "scripts/check-transfer-pilot-health.py")
    health = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(health)
    seed = json.loads((args.seed / "publication.json").read_text())
    manifest = json.loads((args.restored / "publication.json").read_text())
    if manifest["ds_sha256"] != seed["ds_sha256"]:
        raise ValueError("Restored signer differs from original public identity")
    health.load_reference(args.seed / "reference.zone", seed["sha256"], seed["ds_sha256"])
    reference = health.load_reference(args.restored / "reference.zone", manifest["sha256"], seed["ds_sha256"])
    nsd = args.tools_root / "usr/sbin/nsd"
    if hashlib.sha256(nsd.read_bytes()).hexdigest() != NSD_SHA256:
        raise ValueError("Unreviewed authoritative binary")
    env = dict(os.environ, LD_LIBRARY_PATH=str(args.tools_root / "usr/lib/x86_64-linux-gnu"))
    for port in [19954, 19955]:
        for kind in [socket.SOCK_DGRAM, socket.SOCK_STREAM]:
            with socket.socket(socket.AF_INET, kind) as probe:
                probe.bind(("127.0.0.1", port))
    nsd_config = args.work_dir / "nsd.conf"
    nsd_config.write_text(f'''server:
    ip-address: 127.0.0.1@19954
    do-ip6: no
    server-count: 1
    username: ""
    chroot: ""
    zonesdir: "{args.restored.resolve()}"
    zonelistfile: ""
    pidfile: "{args.work_dir.resolve()}/nsd.pid"
    xfrdfile: "{args.work_dir.resolve()}/xfrd.state"
    xfrdir: "{args.work_dir.resolve()}"
remote-control:
    control-enable: no
zone:
    name: {ORIGIN}
    zonefile: reference.zone
''')
    unbound_config = args.work_dir / "unbound.conf"
    unbound_config.write_text(f'''server:
    interface: 127.0.0.1
    port: 19955
    username: ""
    chroot: ""
    directory: "{args.work_dir.resolve()}"
    pidfile: "{args.work_dir.resolve()}/unbound.pid"
    use-syslog: no
    do-ip6: no
    do-not-query-localhost: no
    module-config: "validator iterator"
    trust-anchor-file: "{(args.seed / 'trust-anchor.key').resolve()}"
    val-permissive-mode: no
    val-sig-skew-min: 0
    val-sig-skew-max: 0
    serve-expired: no
    qname-minimisation: no
    aggressive-nsec: no
    trust-anchor-signaling: no
remote-control:
    control-enable: no
stub-zone:
    name: {ORIGIN}
    stub-addr: 127.0.0.1@19954
    stub-prime: no
    stub-first: no
''')
    processes, logs = [], []
    report = {"scope": "current restored lab signatures; original lab anchor, not public parent chain",
              "same_public_ds": True, "observations": [],
              "restored_signer_publication_validated": False,
              "original_anchor_resolver_cases": []}
    try:
        for binary, config, port in [(nsd, nsd_config, 19954),
                                     (args.tools_root / "usr/sbin/unbound", unbound_config, 19955)]:
            log = (args.work_dir / (str(port) + ".log")).open("w")
            logs.append(log)
            process = subprocess.Popen([str(binary), "-d", "-c", str(config)],
                                       env=env, stdout=log, stderr=log, start_new_session=True)
            processes.append(process)
            deadline = time.monotonic() + 5
            while True:
                if process.poll() is not None:
                    raise RuntimeError("Isolated DNS process exited")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=.2):
                        break
                except OSError:
                    if time.monotonic() > deadline:
                        raise RuntimeError("Isolated listener did not start")
                    time.sleep(.1)
        for tcp in [False, True]:
            for name, kind in health.CASES:
                query = dns.message.make_query(name, kind, want_dnssec=True)
                query.flags &= ~dns.flags.RD
                response = (dns.query.tcp if tcp else dns.query.udp)(
                    query, "127.0.0.1", port=19954, timeout=2)
                result = health.evaluate_response(response, name, kind, reference, time.time(), 86400)
                report["observations"].append(dict(result, transport="TCP" if tcp else "UDP"))
                if result["condition"] != "healthy":
                    raise RuntimeError("Restored signed data is unhealthy")
        validating = report["original_anchor_resolver_cases"]
        for name, kind, rcode in [(ORIGIN, "A", "NOERROR"),
                                  ("_canary." + ORIGIN, "TXT", "NOERROR"),
                                  ("absent." + ORIGIN, "A", "NXDOMAIN"),
                                  ("mail." + ORIGIN, "TXT", "NOERROR"),
                                  ("probe.wild." + ORIGIN, "A", "NOERROR")]:
            response = dns.query.udp(dns.message.make_query(name, kind, want_dnssec=True),
                                     "127.0.0.1", port=19955, timeout=3)
            passed = dns.rcode.to_text(response.rcode()) == rcode and bool(response.flags & dns.flags.AD)
            validating.append({"name": name, "type": kind, "passed": passed,
                               "rcode": dns.rcode.to_text(response.rcode()),
                               "ad": bool(response.flags & dns.flags.AD)})
            if not passed:
                raise RuntimeError("Original-anchor resolver did not validate restored publication")
        report.update({"original_anchor_resolver_cases": validating,
                       "restored_signer_publication_validated": True})
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)
        for log in logs:
            log.close()
        report.update({"all_owned_processes_stopped": all(p.poll() is not None for p in processes),
                       "observed_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       "elapsed_seconds_cached_tools": round(time.monotonic() - started, 3)})
        (args.work_dir / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "observations"}))


if __name__ == "__main__":
    main()
