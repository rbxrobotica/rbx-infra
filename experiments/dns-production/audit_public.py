#!/usr/bin/env python3
"""Bounded public observation only; never changes DNS, keys or provider accounts.

Recursive resolvers are transport sources, not independent monitoring origins.
delv performs its own root-chain validation; AD flags alone never qualify it.
"""

from __future__ import annotations

import argparse
import concurrent.futures
from datetime import datetime, timezone
import errno
import hashlib
import ipaddress
import json
from pathlib import Path
import socket
import subprocess
import time

import dns.dnssec
import dns.exception
import dns.flags
import dns.message
import dns.name
import dns.query
import dns.rcode
import dns.rdataclass
import dns.rdatatype
import dns.rrset

ROOT = Path(__file__).resolve().parent
RESOLVERS = ("1.1.1.1", "8.8.8.8")
DELV = "/usr/bin/delv"


def utc():
    return datetime.now(timezone.utc).isoformat()


def rrsets(message, name, kind, sections=("answer",)):
    name = dns.name.from_text(name)
    kind = dns.rdatatype.from_text(kind)
    return [r for section in sections for r in getattr(message, section)
            if r.name == name and r.rdtype == kind]


def values(message, name, kind, sections=("answer",)):
    return sorted({r.to_text().lower() for rr in rrsets(message, name, kind, sections)
                   for r in rr})


def ask(server, name, kind, *, tcp=False, recursive=False, timeout=2):
    q = dns.message.make_query(name, kind, want_dnssec=True, payload=1232)
    if not recursive:
        q.flags &= ~dns.flags.RD
    fallback = False
    try:
        response = (dns.query.tcp if tcp else dns.query.udp)(q, server, timeout=timeout)
    except dns.exception.Timeout:
        if tcp or not recursive:
            raise
        response = dns.query.tcp(q, server, timeout=timeout)
        fallback = True
    if not tcp and response.flags & dns.flags.TC:
        response = dns.query.tcp(q, server, timeout=timeout)
        fallback = True
    if response.flags & dns.flags.TC:
        raise ValueError("truncated_tcp_response")
    if response.rcode() != dns.rcode.NOERROR:
        raise ValueError("rcode_" + dns.rcode.to_text(response.rcode()))
    return response, fallback


def error_code(error):
    if isinstance(error, OSError) and error.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH):
        return "client_network_unreachable"
    if isinstance(error, ValueError):
        return str(error)[:100]
    return type(error).__name__


def ipv6_route():
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as sock:
            sock.connect(("2001:4860:4860::8888", 53))
        return True
    except OSError:
        return False


def validate_authority(soa, keys, zone, ds_records, *, now=None):
    """Authenticate DNSKEY against parent DS before accepting its SOA signature."""
    if not soa.flags & dns.flags.AA or not keys.flags & dns.flags.AA:
        raise ValueError("not_authoritative")
    origin = dns.name.from_text(zone)
    keysets = rrsets(keys, zone, "DNSKEY")
    key_sigs = [r for r in rrsets(keys, zone, "RRSIG")
                if r.covers == dns.rdatatype.DNSKEY]
    soa_sets = rrsets(soa, zone, "SOA")
    soa_sigs = [r for r in rrsets(soa, zone, "RRSIG")
                if r.covers == dns.rdatatype.SOA]
    if len(keysets) != 1 or len(key_sigs) != 1 or len(soa_sets) != 1 or len(soa_sigs) != 1:
        raise ValueError("missing_or_duplicate_signed_rrset")
    trusted = dns.rrset.RRset(origin, dns.rdataclass.IN, dns.rdatatype.DNSKEY)
    for key in keysets[0]:
        for ds in ds_records:
            try:
                candidate = dns.dnssec.make_ds(origin, key, ds.digest_type)
            except (dns.exception.DNSException, ValueError):
                continue
            if candidate == ds:
                trusted.add(key, keysets[0].ttl)
                break
    if not trusted:
        raise ValueError("no_dnskey_matches_parent_ds")
    dns.dnssec.validate(keysets[0], key_sigs[0], {origin: trusted}, now=now)
    dns.dnssec.validate(soa_sets[0], soa_sigs[0], {origin: keysets[0]}, now=now)
    moment = time.time() if now is None else now
    return min(int(sig.expiration - moment) for r in (key_sigs[0], soa_sigs[0]) for sig in r)


def delv_verdict(returncode, stdout, stderr, expected_signed):
    lines = [s.strip().lower() for s in stdout.splitlines()]
    if returncode != 0:
        return "validation_failed"
    if "; fully validated" in lines:
        return "secure"
    if "; unsigned answer" in lines:
        return "expected_secure_but_insecure" if expected_signed else "insecure"
    return "unqualified_validator_output"


def validate_root_chain(zone, resolver, expected_signed):
    try:
        p = subprocess.run([DELV, "-4", "@" + resolver, zone, "SOA",
                            "+norrcomments", "+nocrypto"],
                           capture_output=True, text=True, timeout=18, check=False)
        return {"resolver": resolver, "verdict": delv_verdict(p.returncode, p.stdout, p.stderr,
                                                              expected_signed),
                "returncode": p.returncode,
                "output_sha256": hashlib.sha256((p.stdout + p.stderr).encode()).hexdigest()}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"resolver": resolver, "verdict": "unqualified", "error": error_code(error)}


def resolve_addresses(name, timeout):
    result = {}
    for kind in ("A", "AAAA"):
        try:
            message, _ = ask(RESOLVERS[0], name, kind, recursive=True, timeout=timeout)
            observed = values(message, name, kind)
            result[kind] = [ip for ip in observed if ipaddress.ip_address(ip).is_global]
            if len(result[kind]) != len(observed):
                result[kind + "_error"] = "non_global_public_authority_address"
        except Exception as error:
            result[kind] = []
            result[kind + "_error"] = error_code(error)
    return result


def parent_observations(zone, expected_ns, expected_ds, timeout):
    result = {"observations": [], "errors": []}
    try:
        parent_query = dns.name.from_text(zone).parent().to_text()
        soa, _ = ask(RESOLVERS[0], parent_query, "SOA", recursive=True, timeout=timeout)
        candidates = [r.name for r in soa.answer + soa.authority
                      if r.rdtype == dns.rdatatype.SOA and
                      dns.name.from_text(parent_query).is_subdomain(r.name)]
        if not candidates:
            raise ValueError("parent_zone_not_discovered")
        parent = max(candidates, key=len).to_text()
        message, _ = ask(RESOLVERS[0], parent, "NS", recursive=True, timeout=timeout)
        names = values(message, parent, "NS")[:2]
        result["parent_zone"] = parent
        if len(names) < 2:
            raise ValueError("fewer_than_two_parent_authorities")
        for name in names:
            ips = resolve_addresses(name, timeout)["A"]
            if not ips:
                result["errors"].append("parent_authority_without_ipv4:" + name)
                continue
            for tcp in (False, True):
                try:
                    ns, fallback = ask(ips[0], zone, "NS", tcp=tcp, timeout=timeout)
                    ds, _ = ask(ips[0], zone, "DS", tcp=tcp, timeout=timeout)
                    if not ds.flags & dns.flags.AA:
                        raise ValueError("parent_ds_not_authoritative")
                    observed_ns = values(ns, zone, "NS", ("answer", "authority"))
                    observed_ds = values(ds, zone, "DS")
                    result["observations"].append({"parent_server": name, "ip": ips[0],
                        "tcp": tcp, "udp_tcp_fallback": fallback,
                        "ns": observed_ns, "ds": observed_ds,
                        "matches_recursive_ns": observed_ns == expected_ns,
                        "matches_recursive_ds": observed_ds == expected_ds,
                        "ds_authoritative": bool(ds.flags & dns.flags.AA),
                        "glue": {kind: {host: values(ns, host, kind, ("additional",))
                                       for host in observed_ns} for kind in ("A", "AAAA")}})
                except Exception as error:
                    result["errors"].append(name + ":" + ("tcp" if tcp else "udp") +
                                            ":" + error_code(error))
    except Exception as error:
        result["errors"].append(error_code(error))
    return result


def audit_zone(zone, expected_signed, current_names, timeout, has_ipv6):
    result = {"zone": zone, "expected_signed": expected_signed, "recursive": [],
              "authorities": [], "findings": []}
    messages = []
    for resolver in RESOLVERS:
        try:
            ns, _ = ask(resolver, zone, "NS", recursive=True, timeout=timeout)
            ds, _ = ask(resolver, zone, "DS", recursive=True, timeout=timeout)
            row = {"resolver": resolver, "ns": values(ns, zone, "NS"),
                   "ds": values(ds, zone, "DS"), "ds_ad_flag": bool(ds.flags & dns.flags.AD)}
            messages.append(ds)
            result["recursive"].append(row)
        except Exception as error:
            result["recursive"].append({"resolver": resolver, "error": error_code(error)})
    complete = [r for r in result["recursive"] if "error" not in r]
    if len(complete) != len(RESOLVERS):
        result["findings"].append("recursive_observation_incomplete")
    if not complete:
        return result
    reference = complete[0]
    result["recursive_ns_ds_consensus"] = all(
        r["ns"] == reference["ns"] and r["ds"] == reference["ds"] for r in complete)
    if not result["recursive_ns_ds_consensus"]:
        result["findings"].append("recursive_ns_ds_disagreement")
    if reference["ns"] != sorted(current_names):
        result["findings"].append("current_authority_inventory_drift")
    if expected_signed and not reference["ds"]:
        result["findings"].append("expected_secure_parent_ds_missing")
    result["parent"] = parent_observations(zone, reference["ns"], reference["ds"], timeout)
    if result["parent"]["errors"] or not result["parent"]["observations"]:
        result["findings"].append("direct_parent_observation_incomplete")
    if any(not p["matches_recursive_ns"] or not p["matches_recursive_ds"]
           for p in result["parent"]["observations"]):
        result["findings"].append("parent_recursive_delegation_disagreement")
    ds_data = [d for rr in rrsets(messages[0], zone, "DS") for d in rr]
    serials, ns_sets = set(), set()
    for host in reference["ns"][:8]:
        addresses = resolve_addresses(host, timeout)
        if not addresses["A"] or any(k.endswith("_error") for k in addresses):
            result["findings"].append("authority_address_discovery_incomplete:" + host)
        for kind in ("A", "AAAA"):
            for ip in addresses[kind][:4]:
                for tcp in (False, True):
                    row = {"host": host, "address": ip, "family": ipaddress.ip_address(ip).version,
                           "tcp": tcp}
                    if kind == "AAAA" and not has_ipv6:
                        row.update(qualified=False, error="client_has_no_ipv6_route")
                        result["authorities"].append(row)
                        continue
                    try:
                        soa, fallback = ask(ip, zone, "SOA", tcp=tcp, timeout=timeout)
                        ns, _ = ask(ip, zone, "NS", tcp=tcp, timeout=timeout)
                        sets = rrsets(soa, zone, "SOA")
                        if not soa.flags & dns.flags.AA or not ns.flags & dns.flags.AA:
                            raise ValueError("not_authoritative")
                        if len(sets) != 1 or len(sets[0]) != 1:
                            raise ValueError("soa_not_unique")
                        row["serial"] = next(iter(sets[0])).serial
                        row["ns"] = values(ns, zone, "NS")
                        serials.add(row["serial"])
                        ns_sets.add(tuple(row["ns"]))
                        if ds_data:
                            keys, _ = ask(ip, zone, "DNSKEY", tcp=tcp, timeout=timeout)
                            row["signature_remaining_seconds"] = validate_authority(soa, keys, zone, ds_data)
                        row.update(qualified=True, udp_tcp_fallback=fallback)
                    except Exception as error:
                        row.update(qualified=False, error=error_code(error))
                    result["authorities"].append(row)
    result["observed_serials"] = sorted(serials)
    result["child_ns_matches_parent"] = bool(ns_sets) and ns_sets == {tuple(reference["ns"])}
    if len(serials) != 1:
        result["findings"].append("authority_serial_disagreement_or_missing")
    if not result["child_ns_matches_parent"]:
        result["findings"].append("parent_child_ns_disagreement")
    if not result["authorities"] or any(not a["qualified"] for a in result["authorities"]):
        result["findings"].append("authority_transport_or_dnssec_unqualified")
    result["root_chain"] = [validate_root_chain(zone, r, expected_signed) for r in RESOLVERS]
    if any(r["verdict"] not in ({"secure"} if expected_signed else {"secure", "insecure"})
           for r in result["root_chain"]):
        result["findings"].append("root_chain_validation_unqualified")
    if any(a.get("signature_remaining_seconds", 86401) <= 86400 for a in result["authorities"]):
        result["findings"].append("signature_remaining_at_or_below_one_day")
    result["public_checks_passed"] = not result["findings"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=2)
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=2)
    args = parser.parse_args()
    if not 0.5 <= args.timeout <= 5:
        parser.error("timeout must be between 0.5 and 5 seconds")
    corpus = json.loads((ROOT / "corpus.json").read_text())
    has_ipv6 = ipv6_route()
    report = {"started_utc": utc(), "read_only": True, "client_ipv6_route": has_ipv6,
              "probe_origins": 1, "recursive_transport_sources": list(RESOLVERS),
              "production_ready": False, "production_mutations": False,
              "limitations": ["One workstation is not an independent monitoring vantage.",
                  "SOA/DNSKEY/NS checks do not inventory or back up all zone contents.",
                  "No transfer, provider-isolation or production signer recovery drill is performed.",
                  "Public observations alone cannot qualify account MFA, key custody or production cutover."]}
    try:
        version = subprocess.run([DELV, "-v"], capture_output=True,
            text=True, timeout=3, check=True)
        report["validator_version"] = (version.stdout or version.stderr).strip()
        report["validator_sha256"] = hashlib.sha256(Path(DELV).read_bytes()).hexdigest()
        report["root_anchor_source"] = "Installed delv default root trust anchor; validation not disabled"
    except (OSError, subprocess.SubprocessError) as error:
        report["validator_error"] = error_code(error)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(audit_zone, zone, signed, corpus["current_authority_names"],
                               args.timeout, has_ipv6) for zone, signed in corpus["zones"].items()]
        report["zones"] = [f.result() for f in futures]
    report["finished_utc"] = utc()
    report["zones_public_checks_passed"] = sum(z.get("public_checks_passed", False) for z in report["zones"])
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"zones": len(report["zones"]),
        "zones_public_checks_passed": report["zones_public_checks_passed"],
        "production_ready": False, "output": str(args.output)}))
    return 0 if report["zones_public_checks_passed"] == len(report["zones"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
