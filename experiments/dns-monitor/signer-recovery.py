#!/usr/bin/env python3
"""Owner-only disposable signer fixture; never writes plaintext private keys."""

import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import pathlib
import resource
import subprocess
import time

import dns.dnssec
import dns.name
import dns.rdatatype
import dns.zone
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

ORIGIN = "cloudns-xfr-pilot.rbxsystems.ch."
ENTRY = "rbx/dns/pilot-signer"


def public_snapshot(key, directory, serial, recovered):
    spec = importlib.util.spec_from_file_location(
        "public_lab_source", pathlib.Path(__file__).parent.parent / "dns-origin/build-fixtures.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    dnskey = dns.dnssec.make_dnskey(key.public_key(), 15, flags=257)
    ds = dns.dnssec.make_ds(ORIGIN, dnskey, "SHA256").to_text()
    now = int(time.time())
    zone = dns.zone.from_text(builder.unsigned(
        serial, "signer-after-restore" if recovered else "signer-before-loss"),
        origin=ORIGIN, relativize=False)
    dns.dnssec.sign_zone(zone, keys=[(key, dnskey)],
                         inception=now - 60, expiration=now + 7 * 86400)
    validations = 0
    for name, node in zone.nodes.items():
        for data in node.rdatasets:
            if data.rdtype == dns.rdatatype.RRSIG:
                continue
            signatures = zone.get_rdataset(name, dns.rdatatype.RRSIG, covers=data.rdtype)
            if signatures is None:
                # Unsigned delegation NS and glue, not authoritative signed data.
                child = dns.name.from_text("child." + ORIGIN)
                if not (name.is_subdomain(child) and
                        (name != child or data.rdtype == dns.rdatatype.NS)):
                    raise ValueError("missing_authoritative_signature")
                continue
            dns.dnssec.validate((name, data), (name, signatures),
                               {zone.origin: zone.get_rdataset(ORIGIN, dns.rdatatype.DNSKEY)})
            validations += 1
    if validations != 24:
        raise ValueError("signed_fixture_content_differs")
    public = zone.to_text(relativize=False, sorted=True).encode("ascii")
    manifest = {"format": "rbx-dns-lab-publication-v1", "synthetic_only": True,
                "origin": ORIGIN, "serial": serial, "sha256": hashlib.sha256(public).hexdigest(),
                "ds_sha256": ds, "warn_before_seconds": 86400,
                "generated_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "valid_until_epoch": now + 7 * 86400,
                "recovered_signer_used": recovered, "signed_rrsets_validated": validations,
                "production_signer_custody_proven": False}
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    (directory / "reference.zone").write_bytes(public)
    (directory / "trust-anchor.key").write_text(f"{ORIGIN} 30 IN DNSKEY {dnskey.to_text()}\n")
    (directory / "publication.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def seed(args):
    entry = pathlib.Path.home() / ".password-store" / (ENTRY + ".gpg")
    if entry.exists():
        raise ValueError("preserve_existing_lab_signer")
    key = ed25519.Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    subprocess.run(["pass", "insert", "--multiline", ENTRY], input=pem,
                   capture_output=True, check=True, timeout=60)
    if not entry.is_file() or not entry.read_bytes():
        raise ValueError("encrypted_custody_missing")
    public = public_snapshot(key, args.public_out, args.serial, False)
    return {"operation": "seed_disposable_lab_signer", "public_ds": public["ds_sha256"],
            "encrypted_blob_sha256": hashlib.sha256(entry.read_bytes()).hexdigest(),
            "private_plaintext_file_written": False, "encrypted_remote_publication_pending": True,
            "public_snapshot": public}


def recover(args):
    ciphertext = args.encrypted_backup.read_bytes()
    if hashlib.sha256(ciphertext).hexdigest() != args.expected_cipher_sha256:
        raise ValueError("ciphertext_pin_mismatch")
    command = ["gpg", "--batch", "--pinentry-mode", "error", "--no-symkey-cache"]
    if args.empty_keyring:
        command += ["--homedir", str(args.empty_keyring), "--no-autostart"]
    result = subprocess.run(command + ["--decrypt"], input=ciphertext,
                            capture_output=True, check=True, timeout=45)
    key = serialization.load_pem_private_key(result.stdout, password=None)
    if not isinstance(key, ed25519.Ed25519PrivateKey):
        raise ValueError("unexpected_signer_algorithm")
    dnskey = dns.dnssec.make_dnskey(key.public_key(), 15, flags=257)
    ds = dns.dnssec.make_ds(ORIGIN, dnskey, "SHA256").to_text()
    if ds != args.expected_ds:
        raise ValueError("recovered_public_pin_mismatch")
    public = public_snapshot(key, args.public_out, args.serial, True)
    return {"operation": "restore_disposable_lab_signer", "same_public_ds": True,
            "encrypted_blob_sha256": hashlib.sha256(ciphertext).hexdigest(),
            "private_plaintext_file_written": False,
            "live_pass_entry_used_during_restore": False,
            "owner_gpg_key_required": True, "founder_independent_recovery_proven": False,
            "public_snapshot": public}


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["seed", "recover"])
    parser.add_argument("--public-out", required=True, type=pathlib.Path)
    parser.add_argument("--serial", required=True, type=int)
    parser.add_argument("--receipt", required=True, type=pathlib.Path)
    parser.add_argument("--encrypted-backup", type=pathlib.Path)
    parser.add_argument("--expected-cipher-sha256")
    parser.add_argument("--expected-ds")
    parser.add_argument("--empty-keyring", type=pathlib.Path)
    args = parser.parse_args()
    if args.receipt.exists() or args.public_out.exists():
        parser.error("Preserve previous results")
    if not 1 <= args.serial <= 0xffffffff:
        parser.error("Serial outside uint32")
    if args.operation == "recover" and not all([
            args.encrypted_backup, args.expected_cipher_sha256, args.expected_ds]):
        parser.error("Recovery requires independently pinned ciphertext and public DS")
    started = time.monotonic()
    try:
        report = seed(args) if args.operation == "seed" else recover(args)
        code = 0
    except Exception as error:
        report, code = {"operation_completed": False, "error_type": type(error).__name__}, 1
    report.update({"exit_code": code, "scope": "owner-only disposable lab signer",
                   "observed_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   "elapsed_seconds": round(time.monotonic() - started, 3)})
    args.receipt.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
