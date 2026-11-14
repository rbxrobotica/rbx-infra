"""Fixed-content lab DNSSEC policy, RFC1982 serials and atomic public state."""

import hashlib
import json
import os
import pathlib
import tempfile
import time

import dns.dnssec
import dns.name
import dns.rdatatype
import dns.zone
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

ORIGIN = 'cloudns-xfr-pilot.rbxsystems.ch.'
MAX_ZONE = 256 * 1024
LIFETIME = 7 * 86400
RENEW_BEFORE = 3 * 86400


def newer(candidate, previous):
    return 0 < ((candidate - previous) & 0xffffffff) < 0x80000000


def next_serial(previous):
    return (previous + 1) & 0xffffffff


def parse(raw):
    if not 0 < len(raw) <= MAX_ZONE:
        raise ValueError('zone_size')
    return dns.zone.from_text(raw.decode('ascii'), origin=ORIGIN,
                              relativize=False, allow_include=False)


def serial(zone):
    return next(iter(zone.get_rdataset(ORIGIN, dns.rdatatype.SOA))).serial


def semantics(zone):
    rows = []
    for owner, node in zone.nodes.items():
        for data in node.rdatasets:
            if data.rdtype == dns.rdatatype.RRSIG:
                continue
            for record in data:
                if data.rdtype == dns.rdatatype.SOA:
                    record = record.replace(serial=0)
                rows.append((owner.to_text(), data.ttl, data.rdclass,
                             data.rdtype, record.to_digestable(zone.origin).hex()))
    return hashlib.sha256(json.dumps(sorted(rows)).encode()).hexdigest()


def policy(directory):
    config = json.loads((directory / 'policy.json').read_text())
    raw = (directory / 'baseline.zone').read_bytes()
    if config.get('origin') != ORIGIN or config.get('synthetic_only') is not True:
        raise ValueError('scope')
    if hashlib.sha256(raw).hexdigest() != config['baseline_sha256']:
        raise ValueError('baseline_hash')
    zone = parse(raw)
    keys = zone.get_rdataset(ORIGIN, dns.rdatatype.DNSKEY)
    if len(keys) != 1 or dns.dnssec.make_ds(zone.origin, next(iter(keys)), 'SHA256').to_text() != config['ds_sha256']:
        raise ValueError('baseline_anchor')
    return config, zone


def validate(zone, baseline, now=None, minimum_remaining=0, floor=None):
    now = int(time.time()) if now is None else now
    if semantics(zone) != semantics(baseline):
        raise ValueError('unsigned_content_or_denial_chain_changed')
    observed = serial(zone)
    base = serial(baseline)
    floor = base if floor is None else floor
    if floor != base and not newer(floor, base):
        raise ValueError('floor_below_or_ambiguous_to_baseline')
    if observed != floor and not newer(observed, floor):
        raise ValueError('serial_rollback_or_ambiguous')
    expirations = []
    validations = 0
    child = dns.name.from_text('child.' + ORIGIN)
    keys = {zone.origin: baseline.get_rdataset(ORIGIN, dns.rdatatype.DNSKEY)}
    for owner, node in zone.nodes.items():
        for data in node.rdatasets:
            if data.rdtype == dns.rdatatype.RRSIG:
                continue
            signatures = zone.get_rdataset(owner, dns.rdatatype.RRSIG, covers=data.rdtype)
            if signatures is None:
                if owner.is_subdomain(child) and (owner != child or data.rdtype == dns.rdatatype.NS):
                    continue
                raise ValueError('missing_signature')
            if len(signatures) != 1 or signatures.ttl != data.ttl:
                raise ValueError('unexpected_signature_set')
            sig = next(iter(signatures))
            if sig.inception > now or sig.expiration <= now + minimum_remaining:
                raise ValueError('signature_time_or_margin')
            dns.dnssec.validate((owner, data), (owner, signatures), keys, now=now)
            expirations.append(sig.expiration)
            validations += 1
    if validations != 24:
        raise ValueError('authoritative_rrset_count')
    return {'serial': observed, 'signed_rrsets_validated': validations,
            'valid_until_epoch': min(expirations), 'semantic_sha256': semantics(zone)}


def renew(raw_key, baseline, previous, now=None):
    now = int(time.time()) if now is None else int(now)
    key = serialization.load_pem_private_key(raw_key, password=None)
    if not isinstance(key, ed25519.Ed25519PrivateKey):
        raise ValueError('key_algorithm')
    expected = next(iter(baseline.get_rdataset(ORIGIN, dns.rdatatype.DNSKEY)))
    if dns.dnssec.make_dnskey(key.public_key(), 15, flags=257) != expected:
        raise ValueError('key_anchor_mismatch')
    zone = parse(baseline.to_text(relativize=False, sorted=True).encode())
    for owner, node in list(zone.nodes.items()):
        node.rdatasets[:] = [d for d in node.rdatasets if d.rdtype not in (dns.rdatatype.RRSIG, dns.rdatatype.NSEC)]
    soa = zone.get_rdataset(ORIGIN, dns.rdatatype.SOA)
    replacement = next(iter(soa)).replace(serial=next_serial(previous))
    soa.clear(); soa.add(replacement, soa.ttl)
    dns.dnssec.sign_zone(zone, keys=[(key, expected)],
                        inception=now - 60, expiration=now + LIFETIME)
    checked = validate(zone, baseline, now, minimum_remaining=LIFETIME-120, floor=previous)
    raw = zone.to_text(relativize=False, sorted=True).encode('ascii')
    return raw, {**checked, 'sha256': hashlib.sha256(raw).hexdigest(),
                 'generated_at_epoch': now, 'origin': ORIGIN, 'synthetic_only': True}


def atomic(path, raw, mode=0o644):
    path = pathlib.Path(path)
    fd, filename = tempfile.mkstemp(prefix='.'+path.name+'.', dir=path.parent)
    temporary = pathlib.Path(filename)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path, value):
    atomic(path, (json.dumps(value, indent=2) + '\n').encode())
