#!/usr/bin/env python3
"""Build public, disposable pre-signed DNS fixtures; private keys stay in memory."""

import argparse
import hashlib
import json
import pathlib
import time

import dns.dnssec
import dns.name
import dns.rdatatype
import dns.zone
from cryptography.hazmat.primitives.asymmetric import ed25519

ORIGIN = "cloudns-xfr-pilot.rbxsystems.ch."
PRIMARY = "161.97.147.76"
AUTHORITIES = ["pns71.cloudns.net.", "pns72.cloudns.com.",
               "pns73.cloudns.net.", "pns74.cloudns.uk."]


def unsigned(serial, phase):
    return f"""$ORIGIN {ORIGIN}
$TTL 30
@ IN SOA origin.{ORIGIN} hostmaster.{ORIGIN} {serial} 30 10 120 30
""" + "".join(f"@ IN NS {ns}\n" for ns in AUTHORITIES) + f"""
origin IN A {PRIMARY}
@ IN A 192.0.2.10
@ IN AAAA 2001:db8::10
@ IN MX 10 mail.{ORIGIN}
@ IN TXT "RBX synthetic transfer pilot; no production data"
@ IN CAA 0 issue ";"
mail IN A 192.0.2.20
mail IN AAAA 2001:db8::20
service IN A 192.0.2.30
service IN AAAA 2001:db8::30
_pilot._tcp IN SRV 10 5 8443 service.{ORIGIN}
*.wild IN A 192.0.2.40
child IN NS ns.child.{ORIGIN}
ns.child IN A 192.0.2.53
ns.child IN AAAA 2001:db8::53
_canary IN TXT "{phase}"
"""


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", required=True, type=pathlib.Path)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    now = int(time.time())
    key = ed25519.Ed25519PrivateKey.generate()
    dnskey = dns.dnssec.make_dnskey(key.public_key(), 15, flags=257)
    origin = dns.name.from_text(ORIGIN)
    ds = dns.dnssec.make_ds(origin, dnskey, "SHA256")
    manifests = {}
    for phase, serial, inception, expiration in [
        ("phase-1", 2026111101, now - 60, now + 3600),
        ("phase-2", 2026111102, now - 60, now + 3600),
        ("expired", 2026111103, now - 3600, now - 60),
    ]:
        zone = dns.zone.from_text(unsigned(serial, phase), origin=origin,
                                  relativize=False)
        dns.dnssec.sign_zone(zone, keys=[(key, dnskey)],
                             inception=inception, expiration=expiration)
        data = zone.to_text(relativize=False, sorted=True)
        path = args.output / (phase + ".zone")
        path.write_text(data)
        manifests[phase] = {"serial": serial, "inception": inception,
                            "expiration": expiration,
                            "sha256": hashlib.sha256(data.encode()).hexdigest()}
        if phase == "phase-1":
            # Tamper RDATA while deliberately retaining the original RRSIG.
            bad = data.replace("IN A 192.0.2.10", "IN A 192.0.2.99")
            assert bad != data
            (args.output / "tampered.zone").write_text(bad)
    (args.output / "trust-anchor.key").write_text(
        f"{ORIGIN} 30 IN DNSKEY {dnskey.to_text()}\n")
    wrong = ds.replace(digest=b"\x00" * len(ds.digest))
    (args.output / "wrong-ds.key").write_text(
        f"{ORIGIN} 30 IN DS {wrong.to_text()}\n")
    (args.output / "manifest.json").write_text(json.dumps({
        "synthetic_only": True, "origin": ORIGIN, "origin_ipv4": PRIMARY,
        "authorities": AUTHORITIES, "algorithm": "ED25519", "ds_sha256": ds.to_text(),
        "soa": {"refresh": 30, "retry": 10, "expire": 120, "negative": 30},
        "generated_at_epoch": now, "private_key_persisted": False,
        "private_key_custody": "Ephemeral test signer discarded after building public snapshots; re-key with a new lab anchor to repeat.",
        "phases": manifests,
    }, indent=2) + "\n")
    print("Built public signed/invalid fixtures and lab trust anchors; no private key exported.")


if __name__ == "__main__":
    main()
