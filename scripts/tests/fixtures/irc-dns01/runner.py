"""Synthetic RFC2136 acceptance; runs only in a networkless disposable container."""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import time

import dns.message
import dns.query
import dns.rcode
import dns.tsigkeyring
import dns.update

ROOT = Path('/tmp/lab')
PARENT = 'rbx.example.'
CHILD = '_acme-challenge.irc.rbx.example.'
SIBLING = 'other.example.'
KEY = 'fixture-irc.'
PORT = 15353


def run(*args, input=None):
    result = subprocess.run(args, input=input, text=True, capture_output=True, timeout=20)
    if result.returncode:
        raise RuntimeError('fixture command failed: ' + args[0])
    return result.stdout


def query(name, kind='TXT'):
    response = dns.query.udp(dns.message.make_query(name, kind), '127.0.0.1',
                             port=PORT, timeout=2)
    return response


def main():
    assert os.environ.get('RBX_DNS01_SYNTHETIC') == '1'
    assert os.geteuid() != 0
    plan_data = Path('/fixture/plan.json').read_bytes()
    plan = json.loads(plan_data)
    # Keep permission semantics from the reviewed candidate. Only identities,
    # names and addresses are mapped into synthetic loopback fixtures.
    assert plan['primary_global_candidate'] == {
        'dnsupdate': 'yes', 'allow-dnsupdate-from': '',
        'lua-dnsupdate-policy-script': ''}
    assert plan['challenge_metadata_candidate'] == {
        'ALLOW-DNSUPDATE-FROM': [plan['issuer_source_ipv4_candidate']],
        'TSIG-ALLOW-DNSUPDATE': [plan['tsig_key_name']],
        'NOTIFY-DNSUPDATE': ['1'], 'SOA-EDIT-DNSUPDATE': ['INCREASE']}
    assert plan['challenge_zone'] == '_acme-challenge.' + plan['domain']
    assert plan['tsig_algorithm'] == 'hmac-sha256.'
    ROOT.mkdir(mode=0o700)
    (ROOT / 'socket').mkdir()
    (ROOT / 'pdns').mkdir()
    pg = '/usr/lib/postgresql/16/bin/'
    run(pg + 'initdb', '-D', str(ROOT / 'db'), '--auth=trust', '--no-locale')
    run(pg + 'pg_ctl', '-D', str(ROOT / 'db'), '-l', str(ROOT / 'postgres.log'),
        '-o', '-k /tmp/lab/socket -h "" -p 15432', '-w', 'start')
    daemon = None
    try:
        run(pg + 'createdb', '-h', str(ROOT / 'socket'), '-p', '15432', 'fixture')
        schemas = list(Path('/usr/share').glob('pdns-backend-pgsql/**/schema.pgsql.sql'))
        if not schemas:
            schemas = list(Path('/usr/share').glob('doc/pdns-backend-pgsql/**/schema.pgsql.sql'))
        assert len(schemas) == 1, 'expected packaged gpgsql schema'
        run(pg + 'psql', '-h', str(ROOT / 'socket'), '-p', '15432', '-d', 'fixture',
            '-v', 'ON_ERROR_STOP=1', '-f', str(schemas[0]))
        (ROOT / 'pdns/pdns.conf').write_text('''launch=gpgsql
gpgsql-host=/tmp/lab/socket
gpgsql-port=15432
gpgsql-dbname=fixture
gpgsql-user=fixture
local-address=127.0.0.1
local-port=15353
socket-dir=/tmp/lab/pdns
primary=yes
dnsupdate=yes
allow-dnsupdate-from=
lua-dnsupdate-policy-script=
disable-syslog=yes
cache-ttl=0
negquery-cache-ttl=0
query-cache-ttl=0
''')
        def util(*args):
            return run('pdnsutil', '--config-dir=/tmp/lab/pdns', *args)
        for zone in (PARENT, CHILD, SIBLING):
            util('create-zone', zone, 'ns.rbx.example.')
        secret = base64.b64encode(secrets.token_bytes(32)).decode()
        util('import-tsig-key', KEY, 'hmac-sha256', secret)
        util('set-meta', CHILD, 'ALLOW-DNSUPDATE-FROM', '127.0.0.1/32')
        util('set-meta', CHILD, 'TSIG-ALLOW-DNSUPDATE', KEY)
        util('set-meta', CHILD, 'SOA-EDIT-DNSUPDATE', 'INCREASE')
        log = (ROOT / 'pdns.log').open('w')
        daemon = subprocess.Popen(['pdns_server', '--config-dir=/tmp/lab/pdns',
                                   '--daemon=no', '--guardian=no'], stdout=log, stderr=log)
        for _ in range(50):
            if daemon.poll() is not None:
                raise RuntimeError('authoritative daemon exited')
            try:
                if query(CHILD, 'SOA').answer:
                    break
            except dns.exception.Timeout:
                pass
            time.sleep(.1)
        else:
            raise RuntimeError('authoritative readiness failed')
        results = []
        def update(label, zone, name, key=secret, source='127.0.0.1', delete=False,
                   expected=True):
            ring = dns.tsigkeyring.from_text({KEY: key}) if key else None
            msg = dns.update.Update(zone, keyring=ring, keyname=KEY if key else None,
                                    keyalgorithm='hmac-sha256')
            if delete:
                msg.delete(name, 'TXT', 'synthetic-challenge')
            else:
                msg.add(name, 60, 'TXT', 'synthetic-challenge')
            try:
                response = dns.query.udp(msg, '127.0.0.1', port=PORT,
                                         source=source, timeout=2)
                accepted = response.rcode() == dns.rcode.NOERROR
                code = dns.rcode.to_text(response.rcode())
            except dns.tsig.PeerBadSignature:
                accepted, code = False, 'BADSIG'
            assert accepted == expected, label + ': unexpected ' + code
            present = bool(query(name).answer)
            assert present == (expected and not delete), label + ': wrong stored data'
            results.append({'case': label, 'rcode': code, 'passed': True})
        update('signed-child-add', CHILD, CHILD)
        update('signed-child-delete', CHILD, CHILD, delete=True)
        update('unsigned-child', CHILD, CHILD, key=None, expected=False)
        update('wrong-secret', CHILD, CHILD,
               key=base64.b64encode(secrets.token_bytes(32)).decode(), expected=False)
        update('wrong-source', CHILD, CHILD, source='127.0.0.2', expected=False)
        update('parent-zone', PARENT, 'forbidden.' + PARENT, expected=False)
        update('sibling-zone', SIBLING, 'forbidden.' + SIBLING, expected=False)
        update('forged-child-zone-parent-owner', CHILD, 'forbidden.' + PARENT,
               expected=False)
        # A final positive check distinguishes policy refusal from a dead daemon.
        update('final-signed-add', CHILD, CHILD)
        update('final-cleanup', CHILD, CHILD, delete=True)
        versions = run('dpkg-query', '-W', '-f=${Package}=${Version}\n',
                       'pdns-server', 'pdns-backend-pgsql', 'postgresql-16')
        assert 'pdns-server=4.8.3' in versions, 'unexpected PowerDNS version'
        print(json.dumps({'status': 'passed', 'backend': 'gpgsql',
                          'candidate_sha256': hashlib.sha256(plan_data).hexdigest(),
                          'packages': versions.splitlines(), 'cases': results,
                          'production_changed': False, 'network': 'none',
                          'secondary_replication_tested': False}))
    finally:
        if daemon is not None:
            daemon.terminate()
            try:
                daemon.wait(timeout=5)
            except subprocess.TimeoutExpired:
                daemon.kill()
                daemon.wait()
        run(pg + 'pg_ctl', '-D', str(ROOT / 'db'), '-m', 'immediate', '-w', 'stop')


if __name__ == '__main__':
    main()
