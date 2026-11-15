#!/usr/bin/env python3
"""Build a public-only, hashed release. No installation or scheduling side effect."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import source_access


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--dependency-root',type=Path,required=True)
    p.add_argument('--workload-qualification',type=Path,required=True)
    args=p.parse_args();source=Path(__file__).resolve().parent;stage=args.stage
    if not stage.is_absolute() or stage.exists():raise ValueError('new_absolute_release_required')
    custody=json.loads(source_access.stored('rbx/object-backup/memory-custody'))
    public=(source/'workload-public.txt').read_bytes().strip()
    qualification=json.loads(args.workload_qualification.read_text())
    if qualification.get('public_key_sha256')!=hashlib.sha256(base64.b64decode(public)).hexdigest():
        raise ValueError('qualification_for_different_signer')
    if qualification.get('isolated_restore_passed') is not True or qualification.get('encrypted_readback_passed') is not True:
        raise ValueError('workload_restore_not_qualified')
    source_ready=False
    if (Path.home()/'.password-store/rbx/object-backup/source-reader.gpg').exists():
        reader=json.loads(source_access.stored('rbx/object-backup/source-reader'))
        source_ready=reader.get('scope_qualified') is True
        broad=source_access.stored(custody['source']['source_access_ref']).decode().splitlines()[0]
        if reader['access_key']==broad:raise ValueError('owner_source_key_refused')
    stage.mkdir(mode=0o700,parents=True);local=stage/'local';local.mkdir(mode=0o755)
    for name in ['runtime.py','access.py','access-plan.json','b2_wire.py','workload-public.txt']:
        shutil.copyfile(source/name,local/name)
    shutil.copyfile(source.parent/'object-corpus/archive.py',local/'archive.py')
    shutil.copyfile(source.parent/'dns-renewal/clock_status.py',local/'clock_status.py')
    exported=subprocess.run(['gpg','--batch','--armor','--export-options','export-minimal',
        '--export',custody['owner_recipient_and_signer_fingerprint']],capture_output=True,check=True,timeout=20).stdout
    if not exported.startswith(b'-----BEGIN PGP PUBLIC KEY BLOCK-----'):
        raise ValueError('public_recipient_export_failed')
    (local/'recipient.asc').write_bytes(exported)
    config={'bucket_id':custody['destination']['bucket_id'],'dataset_id':'C008',
        'source_scope_qualified':source_ready,'workload_restore_qualified':True,
        'owner_private_key_deployed':False,'source_deletion_enabled':False}
    (local/'runtime.json').write_text(json.dumps(config,indent=2)+'\n')
    deps=local/'deps';deps.mkdir()
    packages={'botocore':args.dependency_root/'awscli/botocore',
              **{name:args.dependency_root/name for name in ['jmespath','dateutil','urllib3']}}
    for name,path in packages.items():
        if not path.is_dir() or path.is_symlink():raise ValueError('dependency_missing')
        if any(p.is_symlink() for p in path.rglob('*')):raise ValueError('dependency_symlink_refused')
        shutil.copytree(path,deps/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.pyo'))
    shutil.copyfile(args.dependency_root/'six.py',deps/'six.py')
    manifest={str(p.relative_to(stage)):hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(stage.rglob('*')) if p.is_file()}
    raw=json.dumps(manifest,sort_keys=True,indent=2)+'\n';(stage/'manifest.json').write_text(raw)
    print(json.dumps({'public_release_prepared':True,'source_scope_qualified':source_ready,
        'workload_restore_qualified':True,'scheduled_or_installed':False,
        'release_manifest_sha256':hashlib.sha256(raw.encode()).hexdigest(),
        'files':len(manifest),'runtime_depends_on_disposable_cache':False}))


if __name__=='__main__':
    try:main()
    except Exception as e:
        print(json.dumps({'prepared':False,'error_type':type(e).__name__}));raise SystemExit(1)
