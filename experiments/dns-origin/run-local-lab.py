#!/usr/bin/env python3
"""Run the fixed public DNS lab; never install tools or change host DNS/firewalls.

Only the new --work-dir is writable. All query destinations and the zone are
fixed here. An enclosing sandbox and publication/provenance checks belong to
the primary executor; this runner does not claim to supply them.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import sys
import time

import dns.exception
import dns.flags
import dns.message
import dns.name
import dns.query
import dns.rdataclass
import dns.rcode
import dns.rdatatype
import dns.zone


ORIGIN = "cloudns-xfr-pilot.rbxsystems.ch."
PRIMARY, SECONDARY, RESOLVER = "127.0.0.1", "127.0.0.2", "127.0.0.1"
AUTH_PORT, RESOLVER_PORT = 15353, 15355
MAX_FILE = 1024 * 1024
DNSSEC_TYPES = {"DNSKEY", "RRSIG", "NSEC", "NSEC3", "NSEC3PARAM", "DS", "CDS", "CDNSKEY"}
FILES = ("manifest.json", "phase-1.zone", "phase-2.zone", "tampered.zone",
         "expired.zone", "trust-anchor.key", "wrong-ds.key")


class LabError(Exception):
    """Fixed failure code; detailed public observations remain in the report."""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def read_public(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_FILE:
        raise LabError("invalid_public_input")
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE + 1)
    if len(data) != info.st_size or len(data) > MAX_FILE:
        raise LabError("public_input_changed_or_oversized")
    return data


def parse_zone(data):
    return dns.zone.from_text(data.decode("ascii"), origin=ORIGIN,
                              relativize=False, allow_include=False)


def zone_manifest(zone):
    groups = {"ordinary": [], "dnssec": []}
    for name, node in zone.nodes.items():
        for rrset in node.rdatasets:
            kind = dns.rdatatype.to_text(rrset.rdtype)
            item = {"name": name.to_text().lower(), "type": kind,
                    "covers": dns.rdatatype.to_text(rrset.covers), "ttl": rrset.ttl,
                    "rdata": sorted(rdata.to_digestable(zone.origin).hex() for rdata in rrset)}
            groups["dnssec" if kind in DNSSEC_TYPES else "ordinary"].append(item)
    result = {}
    for name, values in groups.items():
        values.sort(key=lambda item: (item["name"], item["type"], item["covers"]))
        result[name] = {"sha256": digest(canonical(values)), "rrsets": len(values),
                        "records": sum(len(item["rdata"]) for item in values)}
    result["sha256"] = digest(canonical(groups))
    return result


def quoted(path):
    text = str(path)
    if any(ord(char) < 32 or ord(char) > 126 or char in '\\"' for char in text):
        raise LabError("unsupported_config_path")
    return json.dumps(text)


def put(path, data):
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        stream.write(data if isinstance(data, bytes) else data.encode())


def group_exists(pid):
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False


class Lab:
    def __init__(self, args):
        self.args = args
        self.started = time.monotonic()
        self.deadline = self.started + args.max_seconds
        self.root = args.work_dir
        self.root.mkdir(mode=0o700, parents=False, exist_ok=False)
        self.children = []
        self.report = {
            "synthetic_only": True, "origin": ORIGIN, "started_utc": self.utc(),
            "completed": False, "passed": False, "checks": [], "errors": [],
            "versions": {}, "fixture_hashes": {},
            "limits": {"max_seconds": args.max_seconds, "query_timeout": args.query_timeout,
                       "convergence_seconds": 30, "expiry_margin_seconds": args.expiry_margin,
                       "validation_clock_skew_min_seconds": 0,
                       "validation_clock_skew_max_seconds": 0},
            "limitations": [
                "IP-scoped AXFR ACLs are not TSIG authentication.",
                "Only loopback NSD/Unbound components are tested; no ClouDNS or public parent chain.",
                "No provider-independent discovery, infrastructure failure domain, or production recovery claim.",
                "Fixture remote publication and extracted-package provenance must be verified externally.",
                "This script does not create a network namespace or modify host DNS/firewalls.",
                "Public runtime files and logs are retained in this new work directory.",
            ],
        }
        self.inputs = {name: read_public(args.fixtures / name) for name in FILES}
        self.report["fixture_hashes"] = {name: digest(data) for name, data in self.inputs.items()}
        self.zones = {name: parse_zone(self.inputs[name]) for name in FILES if name.endswith(".zone")}
        self.expected = {name: zone_manifest(zone) for name, zone in self.zones.items()}
        source = json.loads(self.inputs["manifest.json"])
        if source.get("synthetic_only") is not True or source.get("origin") != ORIGIN:
            raise LabError("wrong_fixture_scope")
        for phase in ("phase-1", "phase-2", "expired"):
            if source["phases"][phase]["sha256"] != digest(self.inputs[phase + ".zone"]):
                raise LabError("fixture_hash_mismatch")
        self.expire = self.zones["phase-2.zone"].get_soa().expire
        if self.expire != 120:
            raise LabError("unexpected_soa_expire")
        now = time.time()
        if min(source["phases"][phase]["expiration"] for phase in ("phase-1", "phase-2")) <= now + args.max_seconds:
            raise LabError("valid_fixture_lifetime_too_short")
        if source["phases"]["expired"]["expiration"] >= now:
            raise LabError("expired_fixture_is_not_in_the_past")
        self.report["expected_manifests"] = self.expected
        self.nsd = args.tools_root / "usr/sbin/nsd"
        self.unbound = args.tools_root / "usr/sbin/unbound"
        if not all(path.is_file() and os.access(path, os.X_OK) for path in (self.nsd, self.unbound)):
            raise LabError("extracted_binaries_missing")
        directories = [args.tools_root / suffix for suffix in
                       ("lib/x86_64-linux-gnu", "usr/lib/x86_64-linux-gnu", "lib", "usr/lib")]
        self.env = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
                    "LD_LIBRARY_PATH": ":".join(str(path) for path in directories if path.is_dir())}
        self.save()

    @staticmethod
    def utc():
        return datetime.now(timezone.utc).isoformat()

    def event(self, event_name, **details):
        print(json.dumps({"event": event_name, "elapsed_seconds": round(time.monotonic() - self.started, 3),
                          **details}, sort_keys=True), file=sys.stderr, flush=True)

    def save(self):
        temporary = self.root / "evidence.json.tmp"
        with temporary.open("w") as stream:
            os.chmod(temporary, 0o600)
            json.dump(self.report, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, self.root / "evidence.json")

    def tick(self):
        if time.monotonic() >= self.deadline:
            raise LabError("lab_deadline")
        for child in self.children:
            if child["log"].stat().st_size > MAX_FILE:
                raise LabError("child_log_bound")
            if not child["stopped"] and child["process"].poll() is not None:
                raise LabError("child_exited_unexpectedly")

    def check(self, name, passed, **observed):
        self.report["checks"].append({"name": name, "passed": bool(passed), **observed})
        self.event(name, passed=bool(passed))
        self.save()
        if not passed:
            raise LabError(name + "_failed")

    def spawn(self, name, argv, directory):
        log = self.root / (name + ".log")
        stream = log.open("xb")
        os.chmod(log, 0o600)
        try:
            process = subprocess.Popen([str(value) for value in argv], stdin=subprocess.DEVNULL,
                                       stdout=stream, stderr=subprocess.STDOUT, cwd=directory,
                                       env=self.env, shell=False, start_new_session=True)
        except Exception:
            stream.close()
            raise
        child = {"name": name, "process": process, "stream": stream, "log": log, "stopped": False}
        self.children.append(child)
        self.event("started", process_name=name, pid=process.pid)
        return child

    def stop(self, child):
        if child["stopped"]:
            return True
        process = child["process"]
        try:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            if group_exists(process.pid):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait(timeout=2)
            limit = time.monotonic() + 2
            while group_exists(process.pid) and time.monotonic() < limit:
                time.sleep(0.05)
            gone = not group_exists(process.pid)
            child["stopped"] = gone
            self.report.setdefault("process_cleanup", []).append(
                {"name": child["name"], "returncode": process.returncode, "group_absent": gone})
            return gone
        finally:
            child["stream"].close()

    def version(self, name, binary, flag, required):
        child = self.spawn(name + "-version", [binary, flag], self.root)
        try:
            code = child["process"].wait(timeout=5)
            data = child["log"].read_bytes()
            text = data[:8192].decode("utf-8", errors="replace")
            self.report["versions"][name] = {"returncode": code, "text": text,
                                             "binary_sha256": digest(binary.read_bytes())}
            if len(data) > 8192 or code or re.search(required, text) is None:
                raise LabError("wrong_tool_version")
        finally:
            self.stop(child)

    def reserve_check(self, address, port):
        for kind in (socket.SOCK_DGRAM, socket.SOCK_STREAM):
            with socket.socket(socket.AF_INET, kind) as probe:
                probe.bind((address, port))

    def ready(self, child, address, port):
        limit = min(self.deadline, time.monotonic() + 8)
        while time.monotonic() < limit:
            self.tick()
            try:
                with socket.create_connection((address, port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.1)
        raise LabError("listener_startup_timeout")

    def start_nsd(self, name, address, fixture, secondary=False):
        directory = self.root / name
        directory.mkdir(mode=0o700)
        (directory / "xfr").mkdir(mode=0o700)
        if not secondary:
            put(directory / "origin.zone", self.inputs[fixture])
        lines = ["server:", f"    ip-address: {address}@{AUTH_PORT}", "    do-ip6: no",
                 "    server-count: 1", '    username: ""', '    chroot: ""',
                 f"    zonesdir: {quoted(directory)}", '    zonelistfile: ""',
                 f"    pidfile: {quoted(directory / 'nsd.pid')}",
                 f"    xfrdfile: {quoted(directory / 'xfrd.state')}",
                 f"    xfrdir: {quoted(directory / 'xfr')}", '    logfile: ""',
                 "    verbosity: 1", "    hide-version: yes", "    refuse-any: yes",
                 "remote-control:", "    control-enable: no", "zone:",
                 f"    name: {ORIGIN}",
                 f"    zonefile: {'secondary.zone' if secondary else 'origin.zone'}",
                 "    provide-xfr: 127.0.0.1 NOKEY", "    provide-xfr: 127.0.0.2 NOKEY",
                 f"    outgoing-interface: {address}"]
        if secondary:
            lines += ["    allow-notify: 127.0.0.1 NOKEY",
                      f"    request-xfr: AXFR 127.0.0.1@{AUTH_PORT} NOKEY"]
        else:
            lines += [f"    notify: 127.0.0.2@{AUTH_PORT} NOKEY", "    notify-retry: 2"]
        config = directory / "nsd.conf"
        put(config, "\n".join(lines) + "\n")
        self.reserve_check(address, AUTH_PORT)
        child = self.spawn(name, [self.nsd, "-d", "-c", config], directory)
        child["directory"] = directory
        self.ready(child, address, AUTH_PORT)
        return child

    def query(self, address, port, name, kind, tcp=False, recursive=False, source=PRIMARY):
        if address not in (PRIMARY, SECONDARY) or port not in (AUTH_PORT, RESOLVER_PORT):
            raise LabError("nonlocal_query_refused")
        if name != ORIGIN and not name.endswith("." + ORIGIN):
            raise LabError("out_of_scope_query_refused")
        self.tick()
        question = dns.message.make_query(name, kind, want_dnssec=True)
        if not recursive:
            question.flags &= ~dns.flags.RD
        started = time.monotonic()
        try:
            method = dns.query.tcp if tcp else dns.query.udp
            response = method(question, address, port=port, timeout=self.args.query_timeout, source=source)
            observation = {"qname": name, "qtype": kind, "address": address, "port": port,
                           "source": source, "transport": "TCP" if tcp else "UDP",
                           "rcode": dns.rcode.to_text(response.rcode()),
                           "aa": bool(response.flags & dns.flags.AA), "ad": bool(response.flags & dns.flags.AD),
                           "answers": [{"name": rrset.name.to_text(), "type": dns.rdatatype.to_text(rrset.rdtype),
                                        "ttl": rrset.ttl, "rdata": sorted(item.to_text() for item in rrset)}
                                       for rrset in response.answer]}
        except (dns.exception.Timeout, OSError, EOFError) as error:
            response = None
            observation = {"qname": name, "qtype": kind, "address": address, "port": port,
                           "source": source, "transport": "TCP" if tcp else "UDP",
                           "rcode": None, "aa": None, "ad": None, "network_error": type(error).__name__}
        observation["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return response, observation

    def transfer(self, address):
        self.tick()
        stream = dns.query.xfr(address, ORIGIN, port=AUTH_PORT, source=PRIMARY,
                               timeout=self.args.query_timeout, lifetime=4, relativize=False)
        def bounded():
            size = records = 0
            for count, message in enumerate(stream, 1):
                size += len(message.to_wire())
                records += sum(len(rrset) for rrset in message.answer)
                if count > 128 or size > 4 * MAX_FILE or records > 4096:
                    raise LabError("axfr_bound")
                yield message
        try:
            zone = dns.zone.from_xfr(bounded(), relativize=False, check_origin=True)
            return zone_manifest(zone)
        finally:
            stream.close()

    def wait_manifest(self, name, address, fixture, seconds=30):
        started, last = time.monotonic(), None
        until = min(self.deadline, started + seconds)
        while time.monotonic() < until:
            try:
                actual = self.transfer(address)
                last = {"manifest": actual}
                if actual == self.expected[fixture] and time.monotonic() <= until:
                    self.check(name, True, expected=self.expected[fixture], observed=actual,
                               elapsed_seconds=round(time.monotonic() - started, 3))
                    return
            except (dns.exception.DNSException, OSError, EOFError, ValueError) as error:
                last = {"exception_type": type(error).__name__}
            time.sleep(0.2)
        self.check(name, False, expected=self.expected[fixture], last_observation=last,
                   elapsed_seconds=round(time.monotonic() - started, 3))

    def publish(self, origin, fixture):
        temporary = origin["directory"] / "next.zone"
        put(temporary, self.inputs[fixture])
        os.replace(temporary, origin["directory"] / "origin.zone")
        os.kill(origin["process"].pid, signal.SIGHUP)
        self.event("origin_reload", fixture=fixture)
        self.wait_manifest("origin_loaded_" + fixture, PRIMARY, fixture, seconds=8)

    def unauthorized(self):
        before = self.transfer(PRIMARY)
        self.check("authorized_axfr_before_rejection", before == self.expected["phase-1.zone"], manifest=before)
        response, observed = self.query(PRIMARY, AUTH_PORT, ORIGIN, "AXFR", tcp=True, source="127.0.0.3")
        refused = response is not None and response.rcode() == dns.rcode.REFUSED and not response.answer
        # Always run the healthy after-control, even if the negative failed.
        after = self.transfer(PRIMARY)
        self.check("authorized_axfr_after_rejection", after == self.expected["phase-1.zone"], manifest=after)
        self.check("unauthorized_axfr_explicitly_refused", refused, observation=observed)

    def dnssec_case(self, name, stub, anchor, valid):
        directory = self.root / ("resolver-" + name)
        directory.mkdir(mode=0o700)
        put(directory / "anchor.key", self.inputs[anchor])
        config = directory / "unbound.conf"
        put(config, f'''server:
    interface: 127.0.0.1
    port: {RESOLVER_PORT}
    do-ip6: no
    username: ""
    chroot: ""
    directory: {quoted(directory)}
    pidfile: {quoted(directory / "unbound.pid")}
    logfile: ""
    use-syslog: no
    verbosity: 1
    access-control: 127.0.0.0/8 allow
    do-not-query-localhost: no
    module-config: "validator iterator"
    trust-anchor-file: {quoted(directory / "anchor.key")}
    val-permissive-mode: no
    val-sig-skew-min: 0
    val-sig-skew-max: 0
    harden-dnssec-stripped: yes
    serve-expired: no
    prefetch: no
    qname-minimisation: no
    aggressive-nsec: no
remote-control:
    control-enable: no
stub-zone:
    name: {ORIGIN}
    stub-addr: {stub}@{AUTH_PORT}
    stub-prime: no
    stub-first: no
''')
        self.reserve_check(RESOLVER, RESOLVER_PORT)
        child = self.spawn("unbound-" + name, [self.unbound, "-d", "-c", config], directory)
        observations, passed = [], True
        try:
            self.ready(child, RESOLVER, RESOLVER_PORT)
            cases = [(ORIGIN, "A", "NOERROR", "192.0.2.10")]
            if valid:
                cases += [("absent." + ORIGIN, "A", "NXDOMAIN", None),
                          ("mail." + ORIGIN, "TXT", "NOERROR", None),
                          ("probe.wild." + ORIGIN, "A", "NOERROR", "192.0.2.40")]
            for qname, kind, rcode, data in cases:
                response, observed = self.query(RESOLVER, RESOLVER_PORT, qname, kind, recursive=True)
                if valid:
                    values = [item.to_text() for rrset in response.answer for item in rrset
                              if rrset.name == dns.name.from_text(qname)
                              and rrset.rdtype == dns.rdatatype.from_text(kind)] if response is not None else []
                    ok = (response is not None and observed["rcode"] == rcode and observed["ad"]
                          and (not response.answer if data is None else
                               sorted(values) == [data]))
                else:
                    ok = (response is not None and response.rcode() == dns.rcode.SERVFAIL
                          and not response.answer and not observed["ad"])
                observed["expected_met"] = bool(ok)
                observations.append(observed)
                passed = passed and bool(ok)
        finally:
            closed = self.stop(child)
        self.check("dnssec_" + name, passed and closed, observations=observations,
                   fresh_process=True, process_group_absent=closed, anchor_sha256=digest(self.inputs[anchor]))

    def direct_phase2(self, name, address, require_failure=False, expired=False):
        observations, passed = [], True
        for tcp in (False, True):
            response, observed = self.query(address, AUTH_PORT, ORIGIN, "A", tcp=tcp)
            if require_failure:
                ok = response is None and "network_error" in observed
            elif expired:
                ok = (response is not None and observed["rcode"] in ("SERVFAIL", "REFUSED", "NOTAUTH")
                      and not observed["aa"] and not response.answer)
            else:
                values = [item.to_text() for rrset in response.answer for item in rrset
                          if rrset.rdtype == dns.rdatatype.A] if response is not None else []
                ok = response is not None and observed["rcode"] == "NOERROR" and observed["aa"] and values == ["192.0.2.10"]
            observed["expected_met"] = bool(ok)
            observations.append(observed)
            passed = passed and bool(ok)
        self.check(name, passed, observations=observations)

    def run(self):
        self.version("nsd", self.nsd, "-v", r"\b4\.8\.0\b")
        self.version("unbound", self.unbound, "-V", r"\b1\.19\.2\b")
        origin = self.start_nsd("origin-initial", PRIMARY, "phase-1.zone")
        secondary = self.start_nsd("secondary-initial", SECONDARY, None, secondary=True)
        self.wait_manifest("secondary_initial_full_transfer", SECONDARY, "phase-1.zone")
        self.unauthorized()
        phase2_started = time.monotonic()
        self.publish(origin, "phase-2.zone")
        self.wait_manifest("secondary_phase2_convergence", SECONDARY, "phase-2.zone",
                           seconds=max(0, 30 - (time.monotonic() - phase2_started)))
        self.dnssec_case("valid-secondary", SECONDARY, "trust-anchor.key", True)
        self.check("secondary_stopped_for_negative_origin_cases", self.stop(secondary))
        self.report["secondary_isolation_note"] = (
            "The initial secondary is stopped during invalid origin variants: expired.zone has a higher serial. "
            "A fresh secondary later transfers published phase2, avoiding serial rollback or cached-invalid state.")
        for name, fixture, anchor, valid in (
            ("valid-origin", "phase-2.zone", "trust-anchor.key", True),
            ("tampered", "tampered.zone", "trust-anchor.key", False),
            ("expired", "expired.zone", "trust-anchor.key", False),
            ("wrong-ds", "phase-2.zone", "wrong-ds.key", False),
        ):
            self.publish(origin, fixture)
            self.dnssec_case(name, PRIMARY, anchor, valid)
        self.publish(origin, "phase-2.zone")
        secondary = self.start_nsd("secondary-outage", SECONDARY, None, secondary=True)
        self.wait_manifest("secondary_phase2_before_outage", SECONDARY, "phase-2.zone")
        self.direct_phase2("origin_before_outage", PRIMARY)
        self.check("origin_process_group_stopped", self.stop(origin))
        outage_started = time.monotonic()
        self.report["outage_started_utc"] = self.utc()
        self.direct_phase2("origin_udp_tcp_unavailable", PRIMARY, require_failure=True)
        self.direct_phase2("secondary_answers_before_expiry", SECONDARY)
        until = outage_started + self.expire + self.args.expiry_margin
        while time.monotonic() < until:
            self.tick()
            self.event("waiting_for_real_soa_expiry", remaining_seconds=round(until - time.monotonic(), 1))
            time.sleep(min(5, max(0, until - time.monotonic())))
        self.report["outage_seconds_at_expiry_probe"] = round(time.monotonic() - outage_started, 3)
        self.direct_phase2("secondary_withholds_after_real_expiry", SECONDARY, expired=True)
        recovery_started = time.monotonic()
        restored = self.start_nsd("origin-restored", PRIMARY, "phase-2.zone")
        self.wait_manifest("origin_restored_from_phase2", PRIMARY, "phase-2.zone", seconds=8)
        self.direct_phase2("restored_origin_udp_tcp", PRIMARY)
        self.wait_manifest("secondary_recovers_after_origin_restart", SECONDARY, "phase-2.zone")
        self.direct_phase2("secondary_udp_tcp_after_recovery", SECONDARY)
        self.report["origin_reconstruction_seconds"] = round(time.monotonic() - recovery_started, 3)
        self.report["completed"] = True
        self.save()

    def cleanup(self):
        results = []
        for child in reversed(self.children):
            try:
                results.append(self.stop(child))
            except Exception as error:
                results.append(False)
                self.report["errors"].append({"code": "cleanup_failed", "exception_type": type(error).__name__,
                                               "process": child["name"]})
        self.report["all_task_process_groups_absent"] = all(results)
        self.report["finished_utc"] = self.utc()
        self.report["elapsed_seconds"] = round(time.monotonic() - self.started, 3)
        self.report["passed"] = (self.report["completed"] and not self.report["errors"]
                                 and all(results) and all(item["passed"] for item in self.report["checks"]))
        self.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools-root", required=True, type=Path)
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--max-seconds", type=int, default=360)
    parser.add_argument("--query-timeout", type=float, default=2.0)
    parser.add_argument("--expiry-margin", type=int, default=10)
    args = parser.parse_args()
    if not 240 <= args.max_seconds <= 600 or not 0.2 <= args.query_timeout <= 5 or not 5 <= args.expiry_margin <= 30:
        parser.error("limits outside the fixed lab bounds")
    for name in ("tools_root", "fixtures", "work_dir"):
        setattr(args, name, getattr(args, name).absolute())
    def interrupted(signum, frame):
        raise LabError("interrupted")
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    lab = None
    try:
        lab = Lab(args)
        lab.event("lab_started")
        lab.run()
    except BaseException as error:
        if lab is None:
            print(json.dumps({"passed": False, "error": error.args[0] if isinstance(error, LabError)
                              else "initialization_failed", "exception_type": type(error).__name__}), flush=True)
            return 1
        lab.report["errors"].append({"code": error.args[0] if isinstance(error, LabError) else "lab_exception",
                                      "exception_type": type(error).__name__})
        lab.event("lab_failed", exception_type=type(error).__name__)
    finally:
        if lab is not None:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            lab.cleanup()
    if lab is not None:
        print(json.dumps(lab.report, indent=2, sort_keys=True), flush=True)
        return 0 if lab.report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
