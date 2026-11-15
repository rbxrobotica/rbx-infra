#!/usr/bin/env python3
"""Bounded encrypted memory producer and separate read-only archive witness."""
import argparse
import base64
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
import access
import b2_wire as b2_access

PREFIX = 'memory-auto/v1/'
LIMIT = 8 * 1024 * 1024
RUN = r'\d{8}T\d{6}Z-[a-f0-9]{32}'


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def load(path):
    p = Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_size > LIMIT:
        raise ValueError('unsafe_input')
    return p.read_bytes()


def credential(root, name):
    if name not in ('source', 'b2', 'signer', 'healthchecks'):
        raise ValueError('unknown_credential')
    return load(root / name)


def clock():
    from clock_status import read_clock
    if not read_clock()['synchronized']:
        raise ValueError('clock_not_synchronized')
    return int(time.time())


def signal(root, failure=False):
    url = credential(root, 'healthchecks').decode().strip()
    if not re.fullmatch(r'https://hc-ping\.com/[a-f0-9-]{36}', url):
        raise ValueError('invalid_ping_destination')
    req = urllib.request.Request(url + ('/fail' if failure else ''), data=b'')
    with urllib.request.build_opener(b2_access.NoRedirect).open(req, timeout=15) as response:
        if response.status != 200:
            raise ValueError('heartbeat_not_accepted')


def sign_receipt(body, secret):
    value = dict(body)
    value['signature'] = base64.b64encode(
        Ed25519PrivateKey.from_private_bytes(secret).sign(encoded(body))).decode()
    return encoded(value)


def verify_receipt(raw, public, now, max_age=25200):
    if len(raw) > 16384:
        raise ValueError('oversized_receipt')
    value = json.loads(raw)
    signature = base64.b64decode(value.pop('signature'), validate=True)
    Ed25519PublicKey.from_public_bytes(public).verify(signature, encoded(value))
    if value.get('format') != 'rbx-memory-workload-v1' or value.get('dataset_id') != 'C008':
        raise ValueError('unexpected_receipt_format')
    if not isinstance(value.get('finished_epoch'), int) or not now - max_age <= value['finished_epoch'] <= now + 120:
        raise ValueError('archive_stale_or_future')
    if not isinstance(value.get('started_epoch'), int) or not value['finished_epoch'] - 600 <= value['started_epoch'] <= value['finished_epoch']:
        raise ValueError('invalid_capture_interval')
    if not re.fullmatch(RUN, value.get('run_id', '')):
        raise ValueError('invalid_run_id')
    if value.get('cipher_name') != PREFIX + value['run_id'] + '/archive.tar.gpg':
        raise ValueError('cipher_outside_owned_run')
    for field in ('ciphertext_sha256', 'manifest_sha256'):
        if not re.fullmatch('[a-f0-9]{64}', value.get(field, '')):
            raise ValueError('invalid_hash')
    if not isinstance(value.get('ciphertext_bytes'), int) or not 0 < value['ciphertext_bytes'] <= LIMIT:
        raise ValueError('invalid_ciphertext_size')
    return value


def retained(info, receipt, now):
    retention = info.get('fileRetention', {})
    if not retention.get('isClientAuthorizedToRead'):
        raise ValueError('retention_not_readable')
    value = retention['value']
    # Minimum protection at original upload, plus protection still effective now.
    if (value.get('mode') != 'compliance' or
        value.get('retainUntilTimestamp', 0) < (receipt['finished_epoch'] + 6 * 86400) * 1000 or
        value['retainUntilTimestamp'] <= now * 1000):
        raise ValueError('compliance_retention_not_effective')


class B2:
    def __init__(self, key, bucket_id, role):
        access.admit(role, key, bucket_id)
        auth = b2_access.authorize(key['applicationKeyId'], key['applicationKey'])
        access.admit(role, b2_access.normalized(auth), bucket_id)
        expires = auth.get('applicationKeyExpirationTimestamp', 0)
        if expires <= time.time() * 1000:
            raise ValueError('credential_expired')
        self.rotation_due = expires <= (time.time() + 14 * 86400) * 1000
        info = auth['apiInfo']['storageApi']
        self.base, self.download, self.token = info['apiUrl'], info['downloadUrl'], auth['authorizationToken']
        b2_access.destination(self.download)
        self.bucket_id = bucket_id

    def api(self, name, payload):
        if name not in ('b2_get_upload_url', 'b2_list_file_names', 'b2_get_file_info'):
            raise ValueError('unexpected_runtime_operation')
        return b2_access.request(self.base + '/b2api/v4/' + name, self.token, payload)

    def upload(self, name, raw):
        if not re.fullmatch(PREFIX + RUN + r'/(archive\.tar\.gpg|receipt\.json)', name) or len(raw) > LIMIT:
            raise ValueError('upload_outside_owned_run')
        grant = self.api('b2_get_upload_url', {'bucketId': self.bucket_id})
        b2_access.upload_destination(grant['uploadUrl'])
        # Deliberately omit retention/ACL overrides; existing Compliance default applies.
        request = urllib.request.Request(grant['uploadUrl'], data=raw, headers={
            'Authorization': grant['authorizationToken'],
            'X-Bz-File-Name': urllib.parse.quote(name, safe=''),
            'Content-Type': 'application/octet-stream',
            'X-Bz-Content-Sha1': hashlib.sha1(raw).hexdigest()})
        with urllib.request.build_opener(b2_access.NoRedirect).open(request, timeout=30) as response:
            result = json.loads(response.read(1024 * 1024))
        if result['fileName'] != name or result['contentLength'] != len(raw):
            raise ValueError('upload_response_mismatch')
        return result

    def download_file(self, file_id):
        url = self.download + '/b2api/v4/b2_download_file_by_id?fileId=' + urllib.parse.quote(file_id, safe='')
        b2_access.destination(url)
        request = urllib.request.Request(url, headers={'Authorization': self.token})
        with urllib.request.build_opener(b2_access.NoRedirect).open(request, timeout=30) as response:
            raw = response.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError('download_oversized')
        return raw

    def latest(self):
        start = None; seen = set(); receipts = []
        for _ in range(10):
            page = self.api('b2_list_file_names', {'bucketId': self.bucket_id,
                'prefix': PREFIX, 'maxFileCount': 1000, **({'startFileName': start} if start else {})})
            for value in page['files']:
                if re.fullmatch(PREFIX + RUN + r'/receipt\.json', value['fileName']):
                    receipts.append(value)
            start = page.get('nextFileName')
            if not start:
                break
            if start in seen:
                raise ValueError('archive_pagination_repeated')
            seen.add(start)
        else:
            raise ValueError('archive_listing_bound_reached')
        if not receipts:
            raise ValueError('no_completed_archive')
        return max(receipts, key=lambda x: x['fileName'])


def witness(client, public, now):
    selected = client.latest()
    receipt = verify_receipt(client.download_file(selected['fileId']), public, now)
    if selected['fileName'] != PREFIX + receipt['run_id'] + '/receipt.json':
        raise ValueError('receipt_relocated')
    info = client.api('b2_get_file_info', {'fileId': receipt['cipher_file_id']})
    if info['fileName'] != receipt['cipher_name'] or info['contentLength'] != receipt['ciphertext_bytes']:
        raise ValueError('cipher_identity_mismatch')
    retained(info, receipt, now)
    retained(client.api('b2_get_file_info', {'fileId': selected['fileId']}), receipt, now)
    cipher = client.download_file(receipt['cipher_file_id'])
    if len(cipher) != receipt['ciphertext_bytes'] or digest(cipher) != receipt['ciphertext_sha256']:
        raise ValueError('cipher_readback_mismatch')
    return {'verified': True, 'run_id': receipt['run_id'],
            'ciphertext_sha256': receipt['ciphertext_sha256'], 'manifest_sha256': receipt['manifest_sha256'],
            'archive_age_seconds': now - receipt['finished_epoch'], 'compliance_retention_verified': True,
            'plaintext_restore_performed': False, 'rotation_due': client.rotation_due}


def capture(client, root, release, now):
    import botocore.session
    from botocore.config import Config
    spec = importlib.util.spec_from_file_location('archive', release / 'archive.py')
    archive = importlib.util.module_from_spec(spec); spec.loader.exec_module(archive)
    source = json.loads(credential(root, 'source'))
    if source.get('scope_qualified') is not True:
        raise ValueError('source_scope_not_qualified')
    plan = source['plan']
    if plan['dataset_id'] != 'C008' or not plan['source_endpoint'].startswith('https://'):
        raise ValueError('unexpected_source')
    source_client = botocore.session.get_session().create_client('s3',
        endpoint_url=plan['source_endpoint'], region_name=plan['source_region'],
        aws_access_key_id=source['access_key'], aws_secret_access_key=source['secret_key'],
        config=Config(connect_timeout=5, read_timeout=20, retries={'max_attempts': 2},
                      s3={'addressing_style': 'path'}))
    try:
        raw, report = archive.collect(source_client, plan)
    finally:
        source_client.close()
    # Ephemeral public-only keyring. The owner private key is never needed here.
    with tempfile.TemporaryDirectory() as home:
        os.chmod(home, 0o700)
        common = ['gpg', '--homedir', home, '--batch', '--no-auto-key-retrieve']
        imported = subprocess.run(common + ['--import', str(release / 'recipient.asc')],
                                  capture_output=True, timeout=20)
        if imported.returncode:
            raise ValueError('public_recipient_import_failed')
        encrypted = subprocess.run(common + ['--trust-model', 'always', '--recipient',
            plan['recipient_fingerprint'], '--encrypt', '--output', '-'], input=raw,
            capture_output=True, timeout=45)
        if encrypted.returncode or not 0 < len(encrypted.stdout) <= LIMIT:
            raise ValueError('encryption_failed')
    run_id = dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex
    cipher_name = PREFIX + run_id + '/archive.tar.gpg'
    uploaded = client.upload(cipher_name, encrypted.stdout)
    finished = clock()
    body = {'format': 'rbx-memory-workload-v1', 'dataset_id': 'C008', 'run_id': run_id,
            'started_epoch': now, 'finished_epoch': finished,
            'cipher_name': cipher_name, 'cipher_file_id': uploaded['fileId'],
            'ciphertext_bytes': len(encrypted.stdout), 'ciphertext_sha256': digest(encrypted.stdout),
            'manifest_sha256': report['manifest_sha256'], 'objects': report['objects'],
            'source_bytes': report['source_bytes'], 'atomic_cross_document_snapshot': False}
    closed = sign_receipt(body, base64.b64decode(credential(root, 'signer'), validate=True))
    verify_receipt(closed, base64.b64decode(load(release / 'workload-public.txt')), finished)
    client.upload(PREFIX + run_id + '/receipt.json', closed)
    return {'captured': True, 'run_id': run_id, 'ciphertext_sha256': body['ciphertext_sha256'],
            'objects': report['objects'], 'source_bytes': report['source_bytes'],
            'source_mutations': 0, 'rotation_due': client.rotation_due,
            'encrypted_readback_verified': False, 'plaintext_restore_performed': False}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('role', choices=['capture', 'witness'])
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    args = parser.parse_args()
    root = Path(os.environ['CREDENTIALS_DIRECTORY'])
    try:
        now = clock()
        config = json.loads(load(args.release / 'runtime.json'))
        client = B2(json.loads(credential(root, 'b2')), config['bucket_id'],
                    'writer' if args.role == 'capture' else 'verifier')
        if args.role == 'capture':
            report = capture(client, root, args.release, now)
        else:
            report = witness(client, base64.b64decode(load(args.release / 'workload-public.txt')), now)
        report['finished_utc'] = dt.datetime.now(dt.timezone.utc).isoformat()
        args.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = args.state_dir / 'last-success.json'
        temporary = args.state_dir / 'last-success.new'
        temporary.write_bytes(encoded(report)); os.replace(temporary, target)
        # Rotation warning still preserves a completed backup, but needs owner attention.
        signal(root, failure=client.rotation_due)
        print(json.dumps(report))
    except Exception as error:
        try: signal(root, failure=True)
        except Exception: pass
        print(json.dumps({'completed': False, 'error_type': type(error).__name__}))
        return 1
    return 0


if __name__ == '__main__': raise SystemExit(main())
