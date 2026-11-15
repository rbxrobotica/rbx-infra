#!/usr/bin/env python3
"""Qualify the workload format using the already trusted manual snapshot.

This never contacts the source or calls a snapshot fresh. No real monitor ping.
Only a new immutable pair is added under the declared B2 worker prefix.
"""
import argparse
import base64
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import runtime
import source_access


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive',type=Path,required=True);p.add_argument('--work-dir',type=Path,required=True)
    p.add_argument('--image',required=True);p.add_argument('--markdown-package',type=Path,required=True)
    p.add_argument('--mdurl-package',type=Path,required=True);p.add_argument('--apply',action='store_true')
    args=p.parse_args();source=Path(__file__).resolve().parent
    # Owner source-tree CLI; installed runtime receives this module in its release.
    sys.path.insert(0,str(source.parent/'dns-renewal'))
    custody=json.loads(source_access.stored('rbx/object-backup/memory-custody'))
    report={'operation':'qualify workload format from the previously validated manual snapshot',
        'source_read_or_write_attempted':False,'fresh_source_capture':False,'monitor_ping_sent':False,
        'apply_requested':args.apply,'source_snapshot_finished_utc':custody['capture']['capture_finished_utc']}
    if not args.apply:print(json.dumps(report));return
    if not args.work_dir.is_absolute() or args.work_dir.exists():raise ValueError('new_private_work_dir_required')
    existing=runtime.load(args.archive)
    if runtime.digest(existing)!=custody['capture']['ciphertext_sha256']:raise ValueError('untrusted_manual_cipher')
    args.work_dir.mkdir(mode=0o700,parents=True)
    spec=importlib.util.spec_from_file_location('archive',source.parent/'object-corpus/archive.py')
    archive=importlib.util.module_from_spec(spec);spec.loader.exec_module(archive)
    raw=archive.gpg_decrypt(existing,custody['owner_recipient_and_signer_fingerprint'])
    public=subprocess.run(['gpg','--batch','--armor','--export-options','export-minimal','--export',
        custody['owner_recipient_and_signer_fingerprint']],capture_output=True,check=True,timeout=20).stdout
    with tempfile.TemporaryDirectory() as temp:
        os.chmod(temp,0o700)
        common=['gpg','--homedir',temp,'--batch','--no-auto-key-retrieve']
        subprocess.run(common+['--import'],input=public,capture_output=True,check=True,timeout=20)
        private=subprocess.run(common+['--with-colons','--list-secret-keys'],capture_output=True,check=True,timeout=20)
        if private.stdout.strip():raise ValueError('private_key_in_public_only_encryption_ring')
        cipher=subprocess.run(common+['--trust-model','always','--recipient',
            custody['owner_recipient_and_signer_fingerprint'],'--encrypt','--output','-'],
            input=raw,capture_output=True,check=True,timeout=45).stdout
    if not 0<len(cipher)<=runtime.LIMIT:raise ValueError('cipher_size_bound')
    started=int(dt.datetime.fromisoformat(custody['capture']['capture_started_utc']).timestamp())
    finished=int(dt.datetime.fromisoformat(custody['capture']['capture_finished_utc']).timestamp())
    run_id=dt.datetime.fromtimestamp(started,dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex
    secret=base64.b64decode(source_access.stored('rbx/object-backup/memory-signer').strip(),validate=True)
    public=Ed25519PrivateKey.from_private_bytes(secret).public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    if public!=base64.b64decode((source/'workload-public.txt').read_bytes()):raise ValueError('workload_pin_mismatch')
    proof={'source_snapshot_finished_utc':custody['capture']['capture_finished_utc'],
        'ciphertext_sha256':runtime.digest(cipher),'ciphertext_bytes':len(cipher),
        'manifest_sha256':custody['capture']['manifest_sha256'],'public_key_sha256':runtime.digest(public),
        'fresh_source_capture':False,'run_id':run_id}
    source_access.checkpoint('rbx/object-backup/format-proof',dict(proof,stage='before-upload'))
    fd=os.open(args.work_dir/'format.tar.gpg',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:f.write(cipher);f.flush();os.fsync(f.fileno())
    bucket=custody['destination']['bucket_id'];writer=runtime.B2(json.loads(source_access.stored('rbx/object-backup/b2-writer')),bucket,'writer')
    cipher_name=runtime.PREFIX+run_id+'/archive.tar.gpg';uploaded=writer.upload(cipher_name,cipher)
    body={'format':'rbx-memory-workload-v1','dataset_id':'C008','run_id':run_id,'started_epoch':started,
        'finished_epoch':finished,'cipher_name':cipher_name,'cipher_file_id':uploaded['fileId'],
        'ciphertext_bytes':len(cipher),'ciphertext_sha256':runtime.digest(cipher),
        'manifest_sha256':proof['manifest_sha256'],'objects':custody['capture']['objects'],
        'source_bytes':custody['capture']['source_bytes'],'atomic_cross_document_snapshot':False,
        'provenance':'re-encoded previously owner-validated manual snapshot; no new source capture'}
    closed=runtime.sign_receipt(body,secret);runtime.verify_receipt(closed,public,runtime.clock())
    source_access.checkpoint('rbx/object-backup/format-proof',dict(proof,stage='receipt-before-upload',
        cipher_file_id=uploaded['fileId'],receipt_sha256=runtime.digest(closed)))
    made=writer.upload(runtime.PREFIX+run_id+'/receipt.json',closed)
    verifier=runtime.B2(json.loads(source_access.stored('rbx/object-backup/b2-verifier')),bucket,'verifier')
    checked=runtime.witness(verifier,public,runtime.clock())
    if checked['run_id']!=run_id:raise ValueError('different_latest_run_requires_review')
    downloaded=verifier.download_file(uploaded['fileId']);receipt=verifier.download_file(made['fileId'])
    (args.work_dir/'downloaded.tar.gpg').write_bytes(downloaded);(args.work_dir/'downloaded-receipt.json').write_bytes(receipt)
    recovery=subprocess.run([sys.executable,'-B',str(source/'recover.py'),'--receipt',str(args.work_dir/'downloaded-receipt.json'),
        '--archive',str(args.work_dir/'downloaded.tar.gpg'),'--public-key',str(source/'workload-public.txt'),
        '--image',args.image,'--markdown-package',str(args.markdown_package),'--mdurl-package',str(args.mdurl_package)],
        capture_output=True,check=True,timeout=100)
    restored=json.loads(recovery.stdout)
    if restored['objects_restored']!=custody['capture']['objects'] or not restored['all_payload_hashes_match']:
        raise ValueError('workload_restore_incomplete')
    report.update(proof,encrypted_readback_passed=True,isolated_restore_passed=True,
        public_only_encryption_ring_verified=True,compliance_retention_verified=True,
        objects_restored=restored['objects_restored'],bytes_restored=restored['bytes_restored'],
        markdown_tokens=restored['markdown_tokens'],restore_phase_seconds=restored['restore_phase_seconds'],
        completed_utc=dt.datetime.now(dt.timezone.utc).isoformat(),plaintext_persistent_files_written=False,
        owner_private_key_deployed=False,continuous_capture_enabled=False,full_service_rto_established=False)
    (args.work_dir/'workload-qualification.json').write_text(json.dumps(report,indent=2)+'\n')
    (args.work_dir/'restore-result.json').write_text(json.dumps(restored,indent=2)+'\n')
    source_access.checkpoint('rbx/object-backup/format-proof',dict(proof,stage='readback-and-isolated-restore-passed',
        cipher_file_id=uploaded['fileId'],receipt_file_id=made['fileId'],receipt_sha256=runtime.digest(closed)))
    print(json.dumps(report))


if __name__=='__main__':
    try:main()
    except Exception as e:
        print(json.dumps({'qualification_completed':False,'error_type':type(e).__name__}));raise SystemExit(1)
