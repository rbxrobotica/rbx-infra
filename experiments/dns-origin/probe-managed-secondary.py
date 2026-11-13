#!/usr/bin/env python3
"""Finite direct and cold validating-resolver checks of the named trial zone."""

import argparse
import concurrent.futures
import datetime
import hashlib
import json
import os
import pathlib
import signal
import socket
import subprocess
import time

import dns.dnssec
import dns.flags
import dns.message
import dns.name
import dns.query
import dns.rcode
import dns.rdatatype
import dns.rrset
import dns.zone

ORIGIN = "cloudns-xfr-pilot.rbxsystems.ch."
SERVERS = ["45.83.248.12", "45.83.249.12", "45.83.250.12", "45.83.251.12"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", required=True, type=pathlib.Path)
    parser.add_argument("--tools-root", required=True, type=pathlib.Path)
    parser.add_argument("--work-dir", required=True, type=pathlib.Path)
    parser.add_argument("--phase", choices=["phase-1", "phase-2"], default="phase-2")
    parser.add_argument("--expected-canary", help="Reviewed TXT value for a recovered-signer publication")
    args = parser.parse_args()
    args.work_dir.mkdir(mode=0o700, exist_ok=False)
    source = (args.fixtures / (args.phase + ".zone")).read_bytes()
    zone = dns.zone.from_text(source.decode(), origin=ORIGIN, relativize=False,
                              allow_include=False)
    origin = dns.name.from_text(ORIGIN)
    child = dns.name.from_text("child." + ORIGIN)
    pinned = zone.get_rdataset(origin, dns.rdatatype.DNSKEY)
    manifest = json.loads((args.fixtures / "manifest.json").read_text())
    assert any(dns.dnssec.make_ds(origin, key, "SHA256").to_text() == manifest["ds_sha256"]
               for key in pinned)
    cases = [(name, data) for name, node in zone.nodes.items()
             for data in node.rdatasets if data.rdtype != dns.rdatatype.RRSIG
             and not name.is_subdomain(child)]

    def probe(task):
        ip, tcp, name, expected = task
        result = {"server": ip, "transport": "TCP" if tcp else "UDP",
                  "name": name.to_text(), "type": dns.rdatatype.to_text(expected.rdtype)}
        query = dns.message.make_query(name, expected.rdtype, want_dnssec=True)
        query.flags &= ~dns.flags.RD
        started = time.monotonic()
        try:
            response = (dns.query.tcp if tcp else dns.query.udp)(query, ip, timeout=3)
            actual = next((r for r in response.answer if r.name == name and
                           r.rdtype == expected.rdtype), None)
            sig = next((r for r in response.answer if r.name == name and
                        r.rdtype == dns.rdatatype.RRSIG and
                        r.covers == expected.rdtype), None)
            same = (actual is not None and actual.ttl == expected.ttl and
                    sorted(x.to_digestable(origin) for x in actual) ==
                    sorted(x.to_digestable(origin) for x in expected))
            signed = False
            if actual is not None and sig is not None:
                dns.dnssec.validate(actual, sig, {origin: pinned})
                signed = True
            result.update({"rcode": dns.rcode.to_text(response.rcode()),
                           "aa": bool(response.flags & dns.flags.AA),
                           "exact_data_and_ttl": same, "signature_valid_against_pin": signed,
                           "passed": same and signed and response.rcode() == 0
                           and bool(response.flags & dns.flags.AA)})
        except Exception as error:
            result.update({"passed": False, "error": type(error).__name__})
        result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
        return result

    tasks = [(ip, tcp, name, data) for ip in SERVERS for tcp in (False, True)
             for name, data in cases]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        direct = list(pool.map(probe, tasks))

    env = dict(os.environ, LD_LIBRARY_PATH=str(
        args.tools_root / "usr/lib/x86_64-linux-gnu"))
    validating = []
    for index, ip in enumerate(SERVERS):
        work = args.work_dir / str(index)
        work.mkdir()
        config = work / "unbound.conf"
        anchor = (args.fixtures / "trust-anchor.key").absolute()
        config.write_text(f'''server:
    interface: 127.0.0.1
    port: 18555
    username: ""
    chroot: ""
    directory: "{work.absolute()}"
    pidfile: "{work.absolute()}/unbound.pid"
    use-syslog: no
    logfile: ""
    do-ip6: no
    access-control: 127.0.0.0/8 allow
    module-config: "validator iterator"
    trust-anchor-file: "{anchor}"
    val-permissive-mode: no
    val-sig-skew-min: 0
    val-sig-skew-max: 0
    serve-expired: no
    prefetch: no
    qname-minimisation: no
    aggressive-nsec: no
    trust-anchor-signaling: no
remote-control:
    control-enable: no
stub-zone:
    name: {ORIGIN}
    stub-addr: {ip}
    stub-prime: no
    stub-first: no
''')
        # Never reuse an existing process's listener.
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 18555))
        log = (work / "unbound.log").open("w")
        process = subprocess.Popen([str(args.tools_root / "usr/sbin/unbound"),
                                    "-d", "-c", str(config)], env=env,
                                   stdout=log, stderr=log, start_new_session=True)
        observations = []
        try:
            deadline = time.monotonic() + 5
            while True:
                if process.poll() is not None:
                    raise RuntimeError("Isolated validator exited")
                try:
                    with socket.create_connection(("127.0.0.1", 18555), timeout=.2):
                        break
                except OSError:
                    if time.monotonic() > deadline:
                        raise RuntimeError("Isolated validator did not start")
                    time.sleep(.1)
            for name, kind, rcode, value in [
                (ORIGIN, "A", "NOERROR", "192.0.2.10"),
                ("_canary." + ORIGIN, "TXT", "NOERROR", '"' + (args.expected_canary or args.phase) + '"'),
                ("absent." + ORIGIN, "A", "NXDOMAIN", None),
                ("mail." + ORIGIN, "TXT", "NOERROR", None),
                ("probe.wild." + ORIGIN, "A", "NOERROR", "192.0.2.40"),
            ]:
                query = dns.message.make_query(name, kind, want_dnssec=True)
                try:
                    response = dns.query.udp(query, "127.0.0.1", port=18555, timeout=5)
                    values = [item.to_text() for rr in response.answer
                              if rr.rdtype == dns.rdatatype.from_text(kind) for item in rr]
                    observed = {"name": name, "type": kind,
                                "rcode": dns.rcode.to_text(response.rcode()),
                                "ad": bool(response.flags & dns.flags.AD), "values": values}
                    observed["passed"] = (observed["rcode"] == rcode and observed["ad"]
                                          and values == ([] if value is None else [value]))
                except Exception as error:
                    observed = {"name": name, "type": kind, "passed": False,
                                "error": type(error).__name__}
                observations.append(observed)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)
            log.close()
        validating.append({"server": ip, "fresh_validator": True,
                           "observations": observations,
                           "process_stopped": process.poll() is not None})
    passed = (all(r["passed"] for r in direct) and
              all(x["passed"] for row in validating for x in row["observations"]))
    result = {"observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "synthetic_origin": ORIGIN, "fixture_sha256": hashlib.sha256(source).hexdigest(),
              "ds_pin": manifest["ds_sha256"], "phase": args.phase, "passed": passed,
              "direct_checks": direct, "cold_validators": validating,
              "limitations": ["IPv4 authorities from one client vantage.",
                              "Pinned lab anchor, not the public parent chain.",
                              "Delegated child/glue are outside the signed authoritative RRset sweep."]}
    (args.work_dir / "evidence.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"direct_passed": sum(x["passed"] for x in direct),
                      "direct_count": len(direct), "validator_passed":
                      sum(x["passed"] for row in validating for x in row["observations"]),
                      "validator_count": 20, "passed": passed}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
