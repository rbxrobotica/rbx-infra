#!/usr/bin/env python3
"""Owner-host restore proof for a downloaded FlightDeck archive, never production.

Requires a fresh named local PostgreSQL16 container, network=none and tmpfs data.
The private GPG key stays on this host. Plaintext goes only to memory/container.
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
import sys
import tarfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from plausible.backup_runner import run_command, BackupError  # noqa: E402

LIMIT=64*1024*1024
RECIPIENT='88C7C5F7E0FB38128BAA46BC5807B2AAF43E3E41'
DATA_DIRECTORY='/var/lib/postgresql/data'


def validate_target(metadata):
    if not re.fullmatch(r'/?flightdeck-recovery-proof-[a-z0-9-]+',metadata.get('Name','')):
        raise RuntimeError('Dedicated recovery container name required')
    if metadata.get('HostConfig',{}).get('NetworkMode')!='none':
        raise RuntimeError('Recovery container must have no network')
    if (metadata.get('HostConfig',{}).get('Privileged') or
            metadata.get('HostConfig',{}).get('PidMode')=='host' or
            metadata.get('HostConfig',{}).get('IpcMode')=='host' or
            any(m.get('Type') in ('bind','volume') for m in metadata.get('Mounts',[]))):
        raise RuntimeError('Recovery container may not expose host state or persistent volumes')
    memory=metadata.get('HostConfig',{}).get('Tmpfs',{})
    if '/var/lib/postgresql/data' not in memory and not any(m.get('Type')=='tmpfs' and
            m.get('Destination')=='/var/lib/postgresql/data' for m in metadata.get('Mounts',[])):
        raise RuntimeError('Recovery data must be on tmpfs')


def validate_data_directory(configured,resolved):
    # A tmpfs mount alone is insufficient if PGDATA/-D points elsewhere or the
    # admitted path is a symlink into the container's writable disk layer.
    if configured.strip()!=DATA_DIRECTORY or resolved.strip()!=DATA_DIRECTORY:
        raise RuntimeError('Actual PostgreSQL data directory must be the admitted tmpfs')


def read_bundle(plaintext,receipt):
    with tarfile.open(fileobj=io.BytesIO(plaintext),mode='r:') as bundle:
        members=bundle.getmembers()
        if (len(members)!=2 or {m.name for m in members}!={'postgres.dump','manifest.json'} or
                any(not m.isfile() or not 0<m.size<=LIMIT for m in members)):
            raise RuntimeError('Unexpected archive members')
        manifest_raw=bundle.extractfile('manifest.json').read(65537)
        if len(manifest_raw)>65536 or hashlib.sha256(manifest_raw).hexdigest()!=receipt['manifest_sha256']:
            raise RuntimeError('Manifest hash differs')
        manifest=json.loads(manifest_raw)
        source=manifest.get('sources',{}).get('postgres',{})
        if (manifest.get('schema')!='rbx.flightdeck.backup-manifest.v1' or source.get('database')!='flightdeck'
                or source.get('major_version')!=16):
            raise RuntimeError('Archive source is not FlightDeck PostgreSQL16')
        dump=bundle.extractfile('postgres.dump').read(LIMIT+1)
        expected=manifest['files']['postgres.dump']
        if (not dump.startswith(b'PGDMP') or len(dump)!=expected['size_bytes'] or
                hashlib.sha256(dump).hexdigest()!=expected['sha256']):
            raise RuntimeError('Inner PostgreSQL dump integrity differs')
        return dump,manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--receipt',type=Path,required=True)
    parser.add_argument('--container',required=True)
    parser.add_argument('--proof',type=Path,required=True)
    args=parser.parse_args();os.umask(0o077)
    if not re.fullmatch(r'flightdeck-recovery-proof-[a-z0-9-]+',args.container):
        raise RuntimeError('Dedicated local recovery container required')
    if args.proof.parent.stat().st_mode & 0o077:raise RuntimeError('Proof directory must be private')
    receipt=json.loads(args.receipt.read_text())
    if (receipt.get('schema')!='rbx.flightdeck.backup-receipt.v1' or receipt.get('encryption_recipient')!=RECIPIENT
            or not args.archive.is_file() or not 0<args.archive.stat().st_size<=LIMIT):
        raise RuntimeError('Unexpected FlightDeck backup receipt or archive')
    archive=receipt['archive']
    if (not archive.get('key','').startswith('flightdeck/backups/full/') or not archive.get('version_id')
            or archive['version_id']=='null' or hashlib.sha256(args.archive.read_bytes()).hexdigest()!=archive['sha256']):
        raise RuntimeError('Versioned ciphertext integrity is not proven')
    def run(argv,label,maximum=1024*1024):
        return run_command(argv,env=dict(os.environ),timeout=180,label=label,max_bytes=maximum)
    metadata=json.loads(run(['podman','inspect',args.container],'Isolated container inspection'))[0]
    validate_target(metadata)
    container_id=metadata.get('Id','')
    if not re.fullmatch(r'[0-9a-f]{64}',container_id):raise RuntimeError('Immutable container identity required')
    pg=['podman','exec',container_id,'psql','-X','-At','-v','ON_ERROR_STOP=1',
        '-U','restore_owner','-d','flightdeck_restore','-c']
    data_directory=run(pg+["select current_setting('data_directory')"],
                       'Actual PostgreSQL data directory').decode()
    resolved=run(['podman','exec',container_id,'readlink','-f',DATA_DIRECTORY],
                 'Resolved recovery data directory').decode()
    validate_data_directory(data_directory,resolved)
    count=run(pg+["select count(*) from information_schema.tables where table_schema not in ('pg_catalog','information_schema')"],
              'Empty isolated recovery database')
    if count.strip()!=b'0':raise RuntimeError('Refusing to restore over an existing database')
    version=int(run(pg+["select current_setting('server_version_num')"],'Target PostgreSQL version'))
    if not 160000<=version<170000:raise RuntimeError('PostgreSQL16 target required')
    started=time.monotonic()
    plaintext=run(['gpg','--no-options','--batch','--no-tty','--pinentry-mode','error',
                   '--output','-','--decrypt',str(args.archive)],'Owner-host decryption',LIMIT)
    dump,manifest=read_bundle(plaintext,receipt)
    restored=subprocess.run(['podman','exec','-i',container_id,'pg_restore','--username=restore_owner',
        '--dbname=flightdeck_restore','--exit-on-error','--no-owner','--no-privileges'],input=dump,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=180)
    if restored.returncode:raise RuntimeError('Isolated restore failed')
    query="""select json_build_object(
      'schema_migrations',(select json_agg(name order by name) from schema_migrations),
      'actor_count',(select count(*) from actor),'workspace_count',(select count(*) from workspace),
      'mission_count',(select count(*) from mission),'audit_count',(select count(*) from audit_event),
      'ledger_count',(select count(*) from capacity_ledger_entry),'actions_count',(select count(*) from action),
      'signals_count',(select count(*) from signal),'outcomes_count',(select count(*) from outcome),
      'invalid_constraints',(select count(*) from pg_constraint where not convalidated),
      'has_daily_capture',to_regclass('daily_capture') is not null,
      'has_intake_review',to_regclass('intake_weekly_review') is not null)"""
    inventory=json.loads(run(pg+[query],'Restored metadata'))
    if inventory['has_daily_capture']:
        inventory.update(json.loads(run(pg+["select json_build_object('daily_captures',(select count(*) from daily_capture),'machine_captures',(select count(*) from daily_capture where recorded_by_actor_id is not null),'routed_captures',(select count(*) from daily_capture where status='ROUTED'))"],'Restored capture inventory')))
    if inventory['has_intake_review']:
        inventory.update(json.loads(run(pg+["select json_build_object('review_snapshots',(select count(*) from intake_weekly_review))"],'Restored review inventory')))
    proof={'schema':'rbx.flightdeck.restore-proof.v1','restored_at':dt.datetime.now(dt.timezone.utc).isoformat(),
           'archive':archive,'manifest_sha256':receipt['manifest_sha256'],'dump_sha256':hashlib.sha256(dump).hexdigest(),
           'target':{'container':args.container,'container_id':container_id,'database':'flightdeck_restore','network':'none','storage':'tmpfs','major_version':16},
           'inventory':inventory,'restore_seconds':round(time.monotonic()-started,3),
           'production_writes':False,'private_key_in_container':False,'plaintext_on_host_disk':False}
    with args.proof.open('x') as stream:
        json.dump(proof,stream,indent=2,sort_keys=True);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    print(json.dumps({'state':'RESTORED_IN_ISOLATION','proof':str(args.proof),'inventory':inventory,
                      'restore_seconds':proof['restore_seconds']}))


if __name__=='__main__':
    try:main()
    except (BackupError,RuntimeError) as exc:
        print(json.dumps({'error':str(exc)}),file=sys.stderr);sys.exit(1)
    except (OSError,ValueError,KeyError,TypeError,AttributeError,subprocess.SubprocessError,tarfile.TarError):
        print('Isolated FlightDeck recovery failed; source/target details suppressed',file=sys.stderr);sys.exit(1)
