#!/usr/bin/env python3
"""Bounded document capture and an offline, tmpfs-only recovery drill.

Detailed source mappings and payloads exist only in memory or signed ciphertext.
This is an operator-run experiment, not an unattended owner-key service.
"""
import argparse
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import time

LIMIT = 8 * 1024 * 1024
HEADERS = ('ContentType', 'CacheControl', 'ContentDisposition', 'ContentEncoding',
           'ContentLanguage', 'Expires', 'Metadata', 'LastModified', 'ETag', 'VersionId')
READS = frozenset(('list_objects_v2', 'get_object', 'get_object_tagging',
                  'get_bucket_versioning', 'get_bucket_acl', 'get_bucket_policy',
                  'get_bucket_lifecycle_configuration', 'get_bucket_encryption',
                  'get_object_lock_configuration'))
SAFE_ERRORS = frozenset(('invalid_document_key', 'unsafe_document_path',
    'unsafe_or_oversized_input', 'input_grew_past_bound', 'source_write_refused',
    'request_limit', 'duplicate_or_excess_source_objects', 'object_size_bound',
    'conditional_read_identity_missing', 'incomplete_pagination',
    'unexpectedly_small_corpus', 'versioned_history_needs_separate_scope',
    'source_control_read_failed', 'source_changed_during_read', 'corpus_size_bound',
    'source_listing_drift', 'archive_size_bound', 'nonregular_or_large_archive_member',
    'duplicate_or_missing_manifest', 'untrusted_manifest', 'unknown_format',
    'archive_member_set_mismatch', 'duplicate_or_excess_document_identity',
    'unsafe_member_identity', 'payload_integrity_failed', 'empty_document',
    'declared_size_mismatch', 'restored_readback_failed', 'decryption_failed',
    'owner_signature_not_verified', 'invalid_recipient', 'source_credential_unavailable',
    'signed_archive_self_verification_failed', 'private_output_directory_required',
    'network_isolation_missing', 'unexpected_credential_environment',
    'ciphertext_readback_mismatch', 'pinned_owner_signer_required',
    'pinned_local_image_required', 'isolated_restore_failed'))


class SandboxFailure(Exception):
    pass


def safe_error(error):
    result = {'success': False, 'error_type': type(error).__name__}
    if isinstance(error, SandboxFailure):
        result['error_code'] = 'isolated_restore_failed'
        result['sandbox'] = error.args[0]
    elif str(error) in SAFE_ERRORS:
        result['error_code'] = str(error)
    return result


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      default=lambda x: x.isoformat()).encode()


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read_bounded(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= LIMIT:
        raise ValueError('unsafe_or_oversized_input')
    with path.open('rb') as f:
        raw = f.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError('input_grew_past_bound')
    return raw


def key_path(key):
    if not isinstance(key, str) or not key.endswith('.md') or '\\' in key or '\x00' in key:
        raise ValueError('invalid_document_key')
    parts = key.split('/')
    if any(x in ('', '.', '..') for x in parts) or PurePosixPath(key).is_absolute():
        raise ValueError('unsafe_document_path')
    return key


def collect(client, plan):
    """Use conditional reads; reconcile two complete listings, not a transaction."""
    calls = 0

    def read(name, **args):
        nonlocal calls
        if name not in READS:
            raise ValueError('source_write_refused')
        calls += 1
        if calls > 1000:
            raise ValueError('request_limit')
        return getattr(client, name)(Bucket=plan['source_bucket'], **args)

    def listing():
        found = {}; token = None; seen = set()
        while True:
            page = read('list_objects_v2', MaxKeys=100,
                        **({'ContinuationToken': token} if token else {}))
            for obj in page.get('Contents', []):
                key = key_path(obj['Key'])
                if key in found or len(found) >= plan['max_objects']:
                    raise ValueError('duplicate_or_excess_source_objects')
                if not isinstance(obj['Size'], int) or not 0 < obj['Size'] <= plan['max_object_bytes']:
                    raise ValueError('object_size_bound')
                if not isinstance(obj.get('ETag'), str) or not obj['ETag']:
                    raise ValueError('conditional_read_identity_missing')
                found[key] = {x: obj.get(x) for x in ('Size', 'ETag', 'LastModified')}
            if not page.get('IsTruncated'):
                break
            token = page.get('NextContinuationToken')
            if not token or token in seen:
                raise ValueError('incomplete_pagination')
            seen.add(token)
        if len(found) < plan['minimum_expected_objects']:
            raise ValueError('unexpectedly_small_corpus')
        return found

    started = utc()
    first = listing()
    versioning = read('get_bucket_versioning')
    if versioning.get('Status') == 'Enabled':
        raise ValueError('versioned_history_needs_separate_scope')
    controls = {'versioning': {k: v for k, v in versioning.items() if k != 'ResponseMetadata'}}
    gaps = []
    for label, name in [('acl', 'get_bucket_acl'), ('policy', 'get_bucket_policy'),
                        ('lifecycle', 'get_bucket_lifecycle_configuration'),
                        ('encryption', 'get_bucket_encryption'),
                        ('object_lock', 'get_object_lock_configuration')]:
        try:
            result = read(name)
            controls[label] = {k: v for k, v in result.items() if k != 'ResponseMetadata'}
        except Exception as error:
            code = getattr(error, 'response', {}).get('Error', {}).get('Code', '')
            if code not in ('NoSuchBucketPolicy', 'NoSuchLifecycleConfiguration',
                            'ServerSideEncryptionConfigurationNotFoundError',
                            'ObjectLockConfigurationNotFoundError', 'NotImplemented'):
                raise ValueError('source_control_read_failed') from None
            controls[label] = {'configuration_absent_or_unsupported': code}
            gaps.append(label)
    records = []; bodies = {}; total = 0
    for index, (key, item) in enumerate(sorted(first.items())):
        result = read('get_object', Key=key, IfMatch=item['ETag'])
        stream = result['Body']
        try:
            raw = stream.read(plan['max_object_bytes'] + 1)
        finally:
            stream.close()
        if len(raw) != item['Size'] or result.get('ETag') != item['ETag']:
            raise ValueError('source_changed_during_read')
        raw.decode('utf-8', errors='strict')
        total += len(raw)
        if total > plan['max_total_bytes'] or total > LIMIT // 2:
            raise ValueError('corpus_size_bound')
        tag_result = read('get_object_tagging', Key=key)
        member = f'objects/{index:06d}.bin'
        bodies[member] = raw
        records.append({'key': key, 'member': member, 'size': len(raw), 'sha256': sha(raw),
                        'headers': {k: result[k] for k in HEADERS if k in result},
                        'tags': tag_result.get('TagSet', [])})
    if encode(first) != encode(listing()):
        raise ValueError('source_listing_drift')
    manifest = {'format': 'rbx-document-corpus-v1', 'dataset_id': plan['dataset_id'],
                'capture_started_utc': started, 'capture_finished_utc': utc(),
                'source_endpoint': plan['source_endpoint'], 'source_bucket': plan['source_bucket'],
                'source_region': plan['source_region'], 'objects': records, 'controls': controls,
                'configuration_absence_or_unsupported': gaps, 'bytes': total,
                'consistency_model': plan['consistency_model'], 'atomic_cross_document_snapshot': False,
                'historical_versions_included': False, 'source_mutations': 0}
    raw_manifest = encode(manifest)
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for name, raw in [('manifest.json', raw_manifest), *bodies.items()]:
            info = tarfile.TarInfo(name); info.size = len(raw); info.mode = 0o600
            archive.addfile(info, io.BytesIO(raw))
    raw = output.getvalue()
    if len(raw) > LIMIT:
        raise ValueError('archive_size_bound')
    return raw, {'dataset_id': plan['dataset_id'], 'objects': len(records), 'source_bytes': total,
                 'manifest_sha256': sha(raw_manifest), 'source_mutations': 0,
                 'logical_source_read_calls': calls, 'capture_started_utc': started,
                 'capture_finished_utc': manifest['capture_finished_utc'],
                 'atomic_cross_document_snapshot': False,
                 'consistency_model': plan['consistency_model']}


def restore(raw, expected_manifest, root, renderer):
    if len(raw) > LIMIT:
        raise ValueError('archive_size_bound')
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
        members = archive.getmembers()
        if any(not m.isfile() or m.size > LIMIT for m in members):
            raise ValueError('nonregular_or_large_archive_member')
        names = [m.name for m in members]
        if len(set(names)) != len(names) or names.count('manifest.json') != 1:
            raise ValueError('duplicate_or_missing_manifest')
        manifest_raw = archive.extractfile('manifest.json').read(LIMIT + 1)
        if sha(manifest_raw) != expected_manifest:
            raise ValueError('untrusted_manifest')
        manifest = json.loads(manifest_raw)
        if manifest.get('format') != 'rbx-document-corpus-v1':
            raise ValueError('unknown_format')
        records = manifest['objects']
        expected = ['manifest.json'] + [r['member'] for r in records]
        if sorted(names) != sorted(expected) or len(set(expected)) != len(expected):
            raise ValueError('archive_member_set_mismatch')
        if len(records) > 100 or len({r['key'] for r in records}) != len(records):
            raise ValueError('duplicate_or_excess_document_identity')
        validated = []; token_count = 0
        for index, record in enumerate(records):
            key_path(record['key'])
            if record['member'] != f'objects/{index:06d}.bin':
                raise ValueError('unsafe_member_identity')
            payload = archive.extractfile(record['member']).read(LIMIT + 1)
            if len(payload) != record['size'] or sha(payload) != record['sha256']:
                raise ValueError('payload_integrity_failed')
            text = payload.decode('utf-8', errors='strict')
            tokens = renderer.parse(text)
            if not tokens:
                raise ValueError('empty_document')
            token_count += len(tokens)
            validated.append((record, payload))
        if sum(len(v) for _, v in validated) != manifest['bytes']:
            raise ValueError('declared_size_mismatch')
        # No tar extraction. Only validated relative Markdown keys reach tmpfs.
        for record, payload in validated:
            target = root / record['key']; target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(payload)
            if sha(target.read_bytes()) != record['sha256']:
                raise ValueError('restored_readback_failed')
        (root / '_recovery-metadata.json').write_bytes(manifest_raw)
        return {'objects_restored': len(validated), 'bytes_restored': manifest['bytes'],
                'all_payload_hashes_match': True, 'metadata_sidecar_preserved': True,
                'utf8_and_markdown_reads_passed': True, 'markdown_tokens': token_count,
                'source_metadata_applied_to_live_provider': False}


def gpg_decrypt(raw, signed_by=None):
    p = subprocess.run(['gpg', '--batch', '--no-auto-key-retrieve', '--status-fd', '2',
                        '--decrypt'], input=raw, capture_output=True, timeout=45)
    if p.returncode or len(p.stdout) > LIMIT:
        raise ValueError('decryption_failed')
    if signed_by:
        statuses = [line.split() for line in p.stderr.splitlines()]
        valid = [x for x in statuses if len(x) >= 12 and x[:2] == [b'[GNUPG:]', b'VALIDSIG']]
        if not any(signed_by.encode() in (x[2], x[-1]) for x in valid):
            raise ValueError('owner_signature_not_verified')
    return p.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['capture', 'recover', 'sandbox'])
    parser.add_argument('--plan'); parser.add_argument('--output'); parser.add_argument('--archive')
    parser.add_argument('--cipher-sha'); parser.add_argument('--manifest-sha')
    parser.add_argument('--signer'); parser.add_argument('--image')
    parser.add_argument('--markdown-package'); parser.add_argument('--mdurl-package')
    args = parser.parse_args()
    started = time.monotonic()
    if args.command == 'capture':
        import botocore.session
        from botocore.config import Config
        plan = json.loads(gpg_decrypt(read_bounded(args.plan)))
        fingerprint = plan['recipient_fingerprint']
        if not re.fullmatch('[0-9A-F]{40,64}', fingerprint):
            raise ValueError('invalid_recipient')
        def secret(ref):
            p = subprocess.run(['pass', 'show', ref], capture_output=True, timeout=25)
            if p.returncode or not p.stdout.strip():
                raise ValueError('source_credential_unavailable')
            return p.stdout.decode().splitlines()[0]
        client = botocore.session.get_session().create_client('s3',
            endpoint_url=plan['source_endpoint'], region_name=plan['source_region'],
            aws_access_key_id=secret(plan['source_access_ref']),
            aws_secret_access_key=secret(plan['source_secret_ref']),
            config=Config(connect_timeout=5, read_timeout=20, retries={'max_attempts': 2},
                          s3={'addressing_style': 'path'}))
        try:
            raw, report = collect(client, plan)
        finally:
            client.close()
        p = subprocess.run(['gpg', '--batch', '--no-auto-key-retrieve', '--local-user', fingerprint,
                            '--recipient', fingerprint, '--sign', '--encrypt', '--output', '-'],
                           input=raw, capture_output=True, timeout=45)
        if p.returncode or gpg_decrypt(p.stdout, fingerprint) != raw:
            raise ValueError('signed_archive_self_verification_failed')
        target = Path(args.output)
        if not target.is_absolute() or target.parent.is_symlink() or target.parent.stat().st_mode & 0o077:
            raise ValueError('private_output_directory_required')
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(p.stdout); f.flush(); os.fsync(f.fileno())
        report.update(ciphertext_bytes=len(p.stdout), ciphertext_sha256=sha(p.stdout),
                      owner_signature_verified=True, captured_at_utc=utc())
        print(json.dumps(report))
    elif args.command == 'sandbox':
        from markdown_it import MarkdownIt
        if set(os.listdir('/sys/class/net')) != {'lo'}:
            raise ValueError('network_isolation_missing')
        if any(re.search('AWS|CONTABO|BACKBLAZE|SECRET|TOKEN|PASSWORD|GNUPG', k) for k in os.environ):
            raise ValueError('unexpected_credential_environment')
        raw = sys.stdin.buffer.read(LIMIT + 1)
        root = Path('/restore/documents'); root.mkdir(mode=0o700)
        report = restore(raw, args.manifest_sha, root, MarkdownIt('commonmark'))
        report.update(network_interfaces=['lo'], source_credentials_present=False,
                      plaintext_storage='isolated disposable tmpfs', sandbox_elapsed_seconds=round(time.monotonic()-started, 3))
        print(json.dumps(report))
    else:
        cipher = read_bounded(args.archive)
        if len(cipher) > LIMIT or sha(cipher) != args.cipher_sha:
            raise ValueError('ciphertext_readback_mismatch')
        if not args.signer or not re.fullmatch('[0-9A-F]{40,64}', args.signer):
            raise ValueError('pinned_owner_signer_required')
        raw = gpg_decrypt(cipher, args.signer)
        if not args.image or not args.image.startswith('sha256:'):
            raise ValueError('pinned_local_image_required')
        command = ['podman', 'run', '--rm', '--interactive', '--pull=never', '--http-proxy=false', '--network=none', '--read-only',
            '--cap-drop=all', '--security-opt=no-new-privileges', '--user=65534:65534',
            '--memory=256m', '--pids-limit=32',
            '--tmpfs=/restore:rw,noexec,nosuid,nodev,size=16m,mode=1777',
            '--mount', f'type=bind,src={Path(__file__).resolve()},dst=/app/archive.py,ro',
            '--mount', f'type=bind,src={args.markdown_package},dst=/deps/markdown_it,ro',
            '--mount', f'type=bind,src={args.mdurl_package},dst=/deps/mdurl,ro',
            '--env=PYTHONPATH=/deps', args.image, 'python3', '-B', '/app/archive.py',
            'sandbox', '--manifest-sha', args.manifest_sha]
        p = subprocess.run(command, input=raw, capture_output=True, timeout=90)
        if p.returncode:
            detail = {'error_type': 'sandbox_process_failed'}
            try:
                candidate = json.loads(p.stderr)
                if re.fullmatch('[A-Za-z_]{1,80}', candidate.get('error_type', '')):
                    detail['error_type'] = candidate['error_type']
                if candidate.get('error_code') in SAFE_ERRORS:
                    detail['error_code'] = candidate['error_code']
            except (ValueError, TypeError):
                pass
            raise SandboxFailure(detail)
        report = json.loads(p.stdout)
        report.update(ciphertext_sha256=sha(cipher), owner_signature_verified=True,
                      restore_phase_elapsed_seconds=round(time.monotonic()-started, 3),
                      restored_at_utc=utc(), source_provider_available_to_restore=False,
                      full_service_rto_established=False)
        print(json.dumps(report))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Never print SDK messages, paths, object keys, payloads or GPG stderr.
        print(json.dumps(safe_error(error)), file=sys.stderr)
        sys.exit(1)
