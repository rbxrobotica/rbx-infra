#!/usr/bin/env python3
"""Capture and restore only an empty Public Presence bootstrap database.

Source SQL and pg_dump are read-only. Plaintext stays in process memory and an
isolated tmpfs-backed PostgreSQL container. The cache contains ciphertext and a
metadata report only. This is not an external backup or independent-key drill.
"""
import argparse
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import time
import uuid

IMAGE = 'ghcr.io/rbxrobotica/comms-backup@sha256:fc4a6037f035baa0d229bd86f744ceca1ddb720e672004d0b7ffad1209365124'
LIMIT = 8 * 1024 * 1024
# This mount is the disposable container's tmpfs, never the host /tmp.
CONTAINER_TMPFS = '/tmp:rw,nosuid,nodev,size=128m,mode=1777'  # nosec B108
COUNT_SQL = "SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema');"


class DrillError(Exception):
    """Fixed labels only; no subprocess output or source contents."""


def run(argv, *, label, data=None, timeout=120):
    try:
        p = subprocess.run(argv, input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        raise DrillError(label + '_failed') from None
    if p.returncode or len(p.stdout) > LIMIT:
        raise DrillError(label + '_failed')
    return p.stdout


def source_command(target, command, label):
    return run(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                '-o', 'ForwardAgent=no', '-o', 'ConnectTimeout=5', target, command], label=label)


def require_empty(target):
    command = ('sudo -n -u postgres env PGOPTIONS="-c default_transaction_read_only=on" '
               'psql -X -At -v ON_ERROR_STOP=1 -d public_presence -c "' + COUNT_SQL + '"')
    raw = source_command(target, command, 'source_metadata')
    if raw.strip() != b'0':
        raise DrillError('source_is_not_an_empty_bootstrap_database')


def drill(target, fingerprint, directory):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9.-]*', target):
        raise DrillError('invalid_ssh_target')
    if not re.fullmatch(r'[A-F0-9]{40}|[A-F0-9]{64}', fingerprint):
        raise DrillError('invalid_recipient_fingerprint')
    directory = Path(directory)
    if directory.is_symlink():
        raise DrillError('invalid_cache_directory')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.stat().st_mode & 0o077:
        raise DrillError('cache_directory_must_be_private')
    require_empty(target)
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    dump = source_command(target,
        'sudo -n -u postgres env PGOPTIONS="-c default_transaction_read_only=on" '
        'pg_dump --format=custom --no-password --no-owner --no-privileges '
        '--lock-wait-timeout=30000 --dbname=public_presence', 'source_snapshot')
    if not dump.startswith(b'PGDMP'):
        raise DrillError('invalid_custom_format_dump')
    require_empty(target)
    manifest = {'schema':'rbx.public-presence.backup-manifest.v1',
        'source_scope':'empty_application_database', 'created_at':started,
        'consistency':'single_database_snapshot', 'source_read_only':True,
        'sources':{'postgres':{'database':'public_presence','application_tables':0}},
        'files':{'postgres.dump':{'sha256':hashlib.sha256(dump).hexdigest(),'size_bytes':len(dump)}}}
    manifest_bytes = (json.dumps(manifest,sort_keys=True)+'\n').encode()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as bundle:
        for name, data in [('postgres.dump',dump),('manifest.json',manifest_bytes)]:
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(data), 0o600
            bundle.addfile(member,io.BytesIO(data))
    encrypted = run(['gpg','--no-options','--batch','--no-tty','--trust-model','always',
        '--no-encrypt-to','--cipher-algo','AES256','--compress-algo','none',
        '--recipient',fingerprint,'--output','-','--encrypt'],label='encryption',data=stream.getvalue())
    run_id = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex
    archive = directory / (run_id+'.tar.gpg')
    with archive.open('xb') as output:
        os.chmod(archive,0o600)
        output.write(encrypted)
    restore_started = time.monotonic()
    restored_bundle = run(['gpg','--no-options','--batch','--no-tty','--pinentry-mode','error',
        '--output','-','--decrypt',str(archive)],label='decryption')
    with tarfile.open(fileobj=io.BytesIO(restored_bundle),mode='r:') as bundle:
        members = bundle.getmembers()
        if (len(members)!=2 or {m.name for m in members}!={'postgres.dump','manifest.json'}
                or not all(m.isfile() and 0<m.size<=LIMIT for m in members)):
            raise DrillError('unexpected_bundle_members')
        recovered_manifest = bundle.extractfile('manifest.json').read()
        recovered_dump = bundle.extractfile('postgres.dump').read()
    if (recovered_manifest!=manifest_bytes or hashlib.sha256(recovered_dump).hexdigest()
            != manifest['files']['postgres.dump']['sha256']):
        raise DrillError('recovered_hash_mismatch')
    container = None
    try:
        container = run(['podman','run','--detach','--network','none','--read-only','--cap-drop','ALL',
            '--security-opt','no-new-privileges','--memory','512m','--memory-swap','512m','--cpus','1',
            '--tmpfs',CONTAINER_TMPFS,'--entrypoint','sh',IMAGE,'-ec',
            'initdb --auth=trust --username=restore_fixture -D /tmp/pg >/tmp/init.log 2>&1\n'
            'exec postgres -D /tmp/pg -h 127.0.0.1 -p 15432 -k /tmp'],label='isolated_container').decode().strip()
        if not re.fullmatch(r'[0-9a-f]{64}',container):
            container = None
            raise DrillError('invalid_isolated_container_identity')
        ready = False
        for _ in range(50):
            p = subprocess.run(['podman','exec',container,'pg_isready','-h','127.0.0.1',
                '-p','15432','-U','restore_fixture'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                timeout=5,check=False)
            if p.returncode==0:
                ready = True
                break
            time.sleep(0.1)
        if not ready:
            raise DrillError('isolated_postgres_not_ready')
        pg = ['podman','exec',container,'psql','-X','-At','-v','ON_ERROR_STOP=1',
              '-h','127.0.0.1','-p','15432','-U','restore_fixture']
        run(pg+['-d','postgres','-c','CREATE DATABASE restore_fixture'],label='isolated_database')
        run(['podman','exec','-i',container,'pg_restore','--exit-on-error','--no-owner','--no-privileges',
             '-h','127.0.0.1','-p','15432','-U','restore_fixture','-d','restore_fixture'],
            label='isolated_restore',data=recovered_dump)
        count = run(pg+['-d','restore_fixture','-c',COUNT_SQL],label='restored_metadata')
        if count.strip()!=b'0':
            raise DrillError('restored_bootstrap_state_mismatch')
    finally:
        if container is not None:
            run(['podman','rm','--force',container],label='isolated_cleanup',timeout=30)
    report = {'schema':'rbx.public-presence.bootstrap-restore-drill.v1',
        'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),'source_capture_read_only':True,
        'source_application_tables':0,'restored_application_tables':0,'synthetic_source':False,
        'ciphertext_sha256':hashlib.sha256(encrypted).hexdigest(),'ciphertext_bytes':len(encrypted),
        'manifest_sha256':hashlib.sha256(manifest_bytes).hexdigest(),
        'dump_sha256':manifest['files']['postgres.dump']['sha256'],
        'gpg_roundtrip':'verified','postgres_restore':'verified','container_image':IMAGE,
        'restore_network':'none','source_credentials_or_private_keys_mounted':False,
        'isolated_container_removed':True,'restore_seconds':round(time.monotonic()-restore_started,3),
        'external_backup_verified':False,'independent_key_custody':'outside_this_drill',
        'production_mutated':False,'archive_location':'operator_encrypted_cache'}
    receipt = directory / (run_id+'.report.json')
    with receipt.open('x') as output:
        os.chmod(receipt,0o600)
        json.dump(report,output,indent=2)
        output.write('\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ssh-target',required=True)
    parser.add_argument('--recipient',required=True)
    parser.add_argument('--cache-dir',required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(drill(args.ssh_target,args.recipient,args.cache_dir),sort_keys=True))
    except DrillError as exc:
        print(json.dumps({'error':str(exc),'external_backup_verified':False}))
        return 1
    except (OSError,ValueError,tarfile.TarError):
        print(json.dumps({'error':'bootstrap_restore_drill_failed','external_backup_verified':False}))
        return 1
    return 0


if __name__=='__main__':
    raise SystemExit(main())
