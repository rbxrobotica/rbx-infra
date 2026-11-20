#!/usr/bin/env python3
"""One owner-supervised C008 refresh; never an unattended owner-key fallback.

Capture writes ciphertext only. Publish requires its digest receipt in the
remote evidence branch, and uses the existing separate B2 writer/verifier.
Neither operation sends a capture heartbeat or enables a capture timer.
"""
import argparse
import base64
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import re
import resource
import subprocess
import sys
import tempfile
import time
import urllib.parse
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import runtime
import source_access

HERE=Path(__file__).resolve().parent
BASE=Path.home()/'.local/state/rbx-memory-backup-20261120'
SDK_PIN='c451380373f5bbfd55ff34402e12a401757bfa319a1fcf06a3af94cbae3a15f7'
IMAGE='sha256:313cf8f190a1abab00472c1f0c950c10486c22114bcd8aef2a8198fb587a6d18'
BRANCH='codex/memory-backup-20261120'
EVIDENCE=Path('/home/psyctl/.cache/rbx-worktrees/resilience-memory-backup-20261120')


def private_dir(path,new=False):
    path=Path(path)
    if not path.is_absolute() or not path.is_relative_to(BASE) or path.resolve()!=path:
        raise ValueError('outside_owned_refresh_directory')
    if new and path.exists():raise ValueError('preserve_existing_refresh')
    if not BASE.exists():BASE.mkdir(mode=0o700)
    path.mkdir(mode=0o700,exist_ok=not new)
    for p in (BASE,path):
        if p.is_symlink() or p.stat().st_uid!=os.getuid() or p.stat().st_mode&0o077:
            raise ValueError('unsafe_refresh_directory')
    return path


def write_new(path,raw):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(directory)
    finally:os.close(directory)


def verified_sdk(stage):
    stage=Path(stage)
    if stage.resolve()!=stage or stage.is_symlink():raise ValueError('unreviewed_sdk_path')
    raw=(stage/'manifest.json').read_bytes()
    if runtime.digest(raw)!=SDK_PIN:raise ValueError('untrusted_sdk_manifest')
    manifest=json.loads(raw)
    actual={str(p.relative_to(stage)) for p in stage.rglob('*') if p.is_file() and p.name!='manifest.json'}
    if actual!=set(manifest):raise ValueError('sdk_member_drift')
    for name,expected in manifest.items():
        p=stage/name
        if p.is_symlink() or not p.resolve().is_relative_to(stage) or runtime.digest(p.read_bytes())!=expected:
            raise ValueError('sdk_content_drift')
    sys.path.insert(0,str(stage/'local/deps'))


def plan(custody):
    source=custody['source']
    expected={'source_bucket':'rbx-memory','source_endpoint':'https://eu2.contabostorage.com',
      'source_region':'default','source_access_ref':'rbx/s3/access-key','source_secret_ref':'rbx/s3/secret-key'}
    # Existing custody names the original capture; C008 is the governed corpus.
    if custody.get('dataset_id')!='rbx-memory-documents-first-capture' or source!=expected:
        raise ValueError('supervised_source_mapping_drift')
    return {**source,'dataset_id':'C008','max_objects':100,'minimum_expected_objects':31,
      'max_object_bytes':524288,'max_total_bytes':4194304,
      'consistency_model':'conditional reads reconciled against two complete listings; no cross-object transaction'}


def source_guard(model,params,**kwargs):
    allowed={'ListObjectsV2','GetObject','GetObjectTagging','GetBucketVersioning',
      'GetBucketAcl','GetBucketPolicy','GetBucketLifecycleConfiguration','GetBucketEncryption','GetObjectLockConfiguration'}
    if model.name not in allowed or params.get('Bucket')!='rbx-memory':
        raise ValueError('supervised_source_operation_refused')


def source_destination(request,**kwargs):
    p=urllib.parse.urlsplit(request.url)
    parts=urllib.parse.unquote(p.path).split('/')[1:]
    query={k for k,_ in urllib.parse.parse_qsl(p.query,keep_blank_values=True)}
    if (p.scheme!='https' or p.netloc!='eu2.contabostorage.com' or request.method not in ('GET','HEAD')
        or p.fragment or not parts or parts[0]!='rbx-memory'
        or any(x in ('.','..') for x in parts) or any(x=='' for x in parts[:-1])
        or query-{'acl','policy','lifecycle','encryption','object-lock','versioning','tagging',
                    'list-type','max-keys','continuation-token','encoding-type','x-id'}):
        raise ValueError('supervised_source_destination_refused')


def encrypt_public_only(raw,fingerprint):
    if not re.fullmatch('[0-9A-F]{40,64}',fingerprint):raise ValueError('invalid_owner_recipient')
    public=subprocess.run(['gpg','--batch','--armor','--export-options','export-minimal','--export',fingerprint],
        capture_output=True,check=True,timeout=20).stdout
    with tempfile.TemporaryDirectory() as home:
        os.chmod(home,0o700);common=['gpg','--homedir',home,'--batch','--no-auto-key-retrieve']
        subprocess.run(common+['--import'],input=public,capture_output=True,check=True,timeout=20)
        secret=subprocess.run(common+['--with-colons','--list-secret-keys'],capture_output=True,check=True,timeout=20)
        if secret.stdout.strip():raise ValueError('private_key_in_encryption_ring')
        return subprocess.run(common+['--trust-model','always','--recipient',fingerprint,'--encrypt','--output','-'],
            input=raw,capture_output=True,check=True,timeout=45).stdout


def signer():
    secret=base64.b64decode(source_access.stored('rbx/object-backup/memory-signer').strip(),validate=True)
    public=Ed25519PrivateKey.from_private_bytes(secret).public_key().public_bytes(
        serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    if public!=base64.b64decode((HERE/'workload-public.txt').read_bytes().strip(),validate=True):
        raise ValueError('workload_signer_pin_drift')
    return secret,public


def capture(args):
    if not args.allow_supervised_owner_read:raise ValueError('supervised_read_not_selected')
    verified_sdk(args.sdk_stage)
    custody=json.loads(source_access.stored('rbx/object-backup/memory-custody'));selected=plan(custody)
    work=private_dir(args.work_dir,new=True)
    from botocore.session import get_session
    from botocore.config import Config
    key=lambda ref:source_access.stored(ref).decode().splitlines()[0]
    client=get_session().create_client('s3',endpoint_url=selected['source_endpoint'],region_name='default',
      aws_access_key_id=key(selected['source_access_ref']),aws_secret_access_key=key(selected['source_secret_ref']),
      verify=True,config=Config(connect_timeout=5,read_timeout=20,retries={'max_attempts':2},s3={'addressing_style':'path'}))
    client.meta.events.register('before-parameter-build.s3',source_guard)
    client.meta.events.register('before-send.s3',source_destination)
    spec=importlib.util.spec_from_file_location('corpus_archive',HERE.parent/'object-corpus/archive.py')
    archive=importlib.util.module_from_spec(spec);spec.loader.exec_module(archive)
    start=runtime.clock()
    try:raw,observed=archive.collect(client,selected)
    finally:client.close()
    cipher=encrypt_public_only(raw,custody['owner_recipient_and_signer_fingerprint'])
    if not 0<len(cipher)<=runtime.LIMIT:raise ValueError('cipher_size_bound')
    end=runtime.clock()
    if end-start>600:raise ValueError('capture_interval_exceeded')
    run_id=dt.datetime.fromtimestamp(start,dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex
    report={**observed,'run_id':run_id,'started_epoch':start,'finished_epoch':end,
      'ciphertext_sha256':runtime.digest(cipher),'ciphertext_bytes':len(cipher),
      'source_read_only':True,'execution_mode':'owner-supervised one-off',
      'fresh_source_capture':True,'public_only_encryption':True,'plaintext_persistent_files_written':False,
      'capture_timer_enabled':False,'monitor_ping_sent':False}
    write_new(work/'archive.tar.gpg',cipher)
    write_new(work/'capture.json',runtime.encoded(report))
    return report


def trusted_receipt(path):
    path=Path(path)
    if (not path.is_absolute() or path.resolve()!=path or
        not path.is_relative_to(EVIDENCE/'evidence/2026-11-20-memory-refresh') or path.suffix!='.json'):
        raise ValueError('unexpected_evidence_path')
    raw=runtime.load(path)
    repo=Path(subprocess.run(['git','-C',str(path.parent),'rev-parse','--show-toplevel'],
        capture_output=True,check=True,text=True,timeout=10).stdout.strip())
    if repo!=EVIDENCE:raise ValueError('unexpected_evidence_repository')
    branch=subprocess.run(['git','-C',str(repo),'branch','--show-current'],capture_output=True,check=True,text=True,timeout=10).stdout.strip()
    if branch!=BRANCH:raise ValueError('unexpected_evidence_branch')
    origin=subprocess.run(['git','-C',str(repo),'remote','get-url','origin'],capture_output=True,check=True,text=True,timeout=10).stdout.strip()
    if origin not in ('https://github.com/rbxrobotica/rbx-resilience.git','git@github.com:rbxrobotica/rbx-resilience.git'):
        raise ValueError('unexpected_evidence_origin')
    cached=subprocess.run(['git','-C',str(repo),'rev-parse','refs/remotes/origin/'+BRANCH],capture_output=True,check=True,text=True,timeout=10).stdout.strip()
    advertised=subprocess.run(['git','-C',str(repo),'ls-remote','origin','refs/heads/'+BRANCH],
        capture_output=True,check=True,text=True,timeout=30).stdout.split()
    if len(advertised)!=2 or advertised[0]!=cached:raise ValueError('evidence_remote_not_acknowledged')
    pinned=subprocess.run(['git','-C',str(repo),'show',cached+':'+str(path.relative_to(repo))],
        capture_output=True,check=True,timeout=10).stdout
    if raw!=pinned:raise ValueError('receipt_not_remotely_checkpointed')
    return json.loads(raw)


def publish(args):
    report=trusted_receipt(args.capture_receipt)
    if report.get('dataset_id')!='C008' or report.get('fresh_source_capture') is not True or report.get('source_mutations')!=0:
        raise ValueError('unexpected_capture_receipt')
    work=private_dir(args.work_dir);cipher=runtime.load(work/'archive.tar.gpg')
    if runtime.digest(cipher)!=report['ciphertext_sha256'] or len(cipher)!=report['ciphertext_bytes']:
        raise ValueError('untrusted_capture_cipher')
    custody=json.loads(source_access.stored('rbx/object-backup/memory-custody'));plan(custody)
    secret,public=signer();bucket=custody['destination']['bucket_id']
    writer=runtime.B2(json.loads(source_access.stored('rbx/object-backup/b2-writer')),bucket,'writer')
    verifier=runtime.B2(json.loads(source_access.stored('rbx/object-backup/b2-verifier')),bucket,'verifier')
    # Refuse stale captures before publishing anything; preserve original capture time.
    body={k:report[k] for k in ('dataset_id','run_id','started_epoch','finished_epoch','ciphertext_sha256',
      'ciphertext_bytes','manifest_sha256','objects','source_bytes','atomic_cross_document_snapshot')}
    name=runtime.PREFIX+body['run_id']+'/archive.tar.gpg'
    body.update(format='rbx-memory-workload-v1',cipher_name=name,cipher_file_id='pending',
      provenance='owner-supervised fresh read-only capture; not a periodic worker')
    runtime.verify_receipt(runtime.sign_receipt(body,secret),public,runtime.clock())
    # One authorized primary publisher. Never retry an ambiguous or partial run.
    # Exact remote history also prevents repetition after loss of this local claim.
    write_new(work/'publish-started.json',runtime.encoded({'run_id':body['run_id'],
        'ciphertext_sha256':body['ciphertext_sha256'],'started_epoch':runtime.clock()}))
    prior=verifier.api('b2_list_file_versions',{'bucketId':bucket,'prefix':runtime.PREFIX+body['run_id']+'/',
        'maxFileCount':10})
    if prior.get('files') or prior.get('nextFileName'):raise ValueError('preserve_existing_remote_attempt')
    published=writer.upload(name,cipher);body['cipher_file_id']=published['fileId']
    closed=runtime.sign_receipt(body,secret)
    made=writer.upload(runtime.PREFIX+body['run_id']+'/receipt.json',closed)
    now=runtime.clock()
    for file_id,expected_name,expected_bytes in ((published['fileId'],name,len(cipher)),
        (made['fileId'],runtime.PREFIX+body['run_id']+'/receipt.json',len(closed))):
        info=verifier.api('b2_get_file_info',{'fileId':file_id})
        if info['fileName']!=expected_name or info['contentLength']!=expected_bytes:
            raise ValueError('published_version_identity_mismatch')
        runtime.retained(info,body,now)
    observed=runtime.witness(verifier,public,runtime.clock())
    if observed['run_id']!=body['run_id']:raise ValueError('different_latest_run_requires_review')
    downloaded=verifier.download_file(published['fileId']);receipt=verifier.download_file(made['fileId'])
    if downloaded!=cipher or receipt!=closed:raise ValueError('independent_readback_mismatch')
    write_new(work/'downloaded.tar.gpg',downloaded);write_new(work/'downloaded-receipt.json',receipt)
    return {'published':True,'run_id':body['run_id'],'ciphertext_sha256':body['ciphertext_sha256'],
      'manifest_sha256':body['manifest_sha256'],'objects':body['objects'],'source_bytes':body['source_bytes'],
      'capture_finished_epoch':body['finished_epoch'],'independent_readback_passed':True,
      'compliance_retention_verified':True,'rotation_due':observed['rotation_due'],
      'capture_timer_enabled':False,'plaintext_restore_performed':False,'monitor_ping_sent':False}


def main():
    resource.setrlimit(resource.RLIMIT_CORE,(0,0));sys.path.insert(0,str(HERE.parent/'dns-renewal'))
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('operation',choices=['capture','publish'])
    p.add_argument('--work-dir',required=True,type=Path);p.add_argument('--sdk-stage',type=Path)
    p.add_argument('--allow-supervised-owner-read',action='store_true');p.add_argument('--capture-receipt',type=Path)
    args=p.parse_args();print(json.dumps(capture(args) if args.operation=='capture' else publish(args)))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'completed':False,'error_type':type(error).__name__}));raise SystemExit(1)
