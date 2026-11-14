#!/usr/bin/env python3
"""Forced-command SSH gateway: public lab zone only, never administrative access."""

import base64
import fcntl
import hashlib
import json
import os
import pathlib
import signal
import sys
import time

sys.path.insert(0, '/opt/rbx-dns-renewal/vendor')

import dns.flags
import dns.message
import dns.query
import dns.rdatatype

import core
import probe

RELEASE = pathlib.Path('/opt/rbx-dns-renewal')
STATE = pathlib.Path('/var/lib/rbx-dns-renew-origin')
ADDRESS = '161.97.147.76'


def selected(baseline):
    raw = (STATE / 'origin.zone').read_bytes()
    zone = core.parse(raw)
    # An authenticated public old publication may seed recovery after expiry.
    # It is cryptographically checked at its last valid instant, never called
    # live healthy. The new candidate is always validated at real current time.
    expirations = [r.expiration for node in zone.nodes.values()
                   for data in node.rdatasets if data.rdtype == dns.rdatatype.RRSIG for r in data]
    checked = core.validate(zone, baseline, now=min(int(time.time()), min(expirations)-1))
    return raw, checked


def verify_origin(expected, baseline, deadline=15):
    until = time.monotonic() + deadline
    while True:
        try:
            checked = probe.run(baseline,expected,warning=core.RENEW_BEFORE,
                                expected_serial=expected,servers=[ADDRESS])
            if checked['exit_code'] != 0:
                raise ValueError('origin_not_updated')
            return
        except Exception:
            if time.monotonic() >= until:
                raise ValueError('origin_reload_unconfirmed') from None
            time.sleep(0.2)


def publish(raw, baseline):
    _, before = selected(baseline)
    zone = core.parse(raw)
    checked = core.validate(zone, baseline, minimum_remaining=core.RENEW_BEFORE, floor=before['serial'])
    digest = hashlib.sha256(raw).hexdigest()
    same = raw == (STATE / 'origin.zone').read_bytes()
    if not same and not core.newer(checked['serial'], before['serial']):
        raise ValueError('serial_must_advance')
    if not same:
        history = STATE / 'releases'
        history.mkdir(mode=0o750, exist_ok=True)
        archive = history / (digest + '.zone')
        if not archive.exists():
            core.atomic(archive, raw)
        core.atomic(STATE / 'origin.zone', raw)
    # Re-signal also on idempotent retry after an interrupted previous call.
    pid = int(pathlib.Path('/run/rbx-dns-renew-origin/nsd.pid').read_text().strip())
    os.kill(pid, signal.SIGHUP)
    verify_origin(checked['serial'],baseline)
    core.write_json(STATE / 'last-accepted.json', {**checked, 'sha256':digest,
                                                'accepted_at_epoch':int(time.time())})
    return {**checked, 'sha256':digest, 'origin_reload_verified':True, 'idempotent_retry':same}


def main():
    # sshd executes this as an unprivileged dedicated account. No user-supplied
    # command, filename, environment assignment or shell text is executed.
    command = os.environ.get('SSH_ORIGINAL_COMMAND', '')
    if command not in ('inspect', 'publish'):
        raise ValueError('command_not_allowed')
    _, baseline = core.policy(RELEASE)
    with (STATE / 'gateway.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if command == 'inspect':
            raw, report = selected(baseline)
            q = dns.message.make_query(core.ORIGIN, 'SOA', want_dnssec=True)
            response = dns.query.tcp(q, ADDRESS, timeout=2)
            soas = [r for rr in response.answer if rr.rdtype == dns.rdatatype.SOA for r in rr]
            if not response.flags & dns.flags.AA or len(soas)!=1 or soas[0].serial!=report['serial']:
                raise ValueError('selected_origin_not_served')
            report.update({'zone_base64':base64.b64encode(raw).decode(),
                           'sha256':hashlib.sha256(raw).hexdigest(),
                           'expired_now':report['valid_until_epoch'] <= time.time()})
        else:
            raw = sys.stdin.buffer.read(core.MAX_ZONE+1)
            report = publish(raw, baseline)
    print(json.dumps(report))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'completed':False,'error_type':type(error).__name__}))
        raise SystemExit(1)
