#!/usr/bin/env python3
"""Operator-terminal activation: owner pass stays outside all system services."""
import argparse
import base64
import json
from pathlib import Path
import resource
import subprocess
import source_access
import access


def main():
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--public-stage',type=Path,required=True)
    args=p.parse_args();stage=args.public_stage.resolve()
    source=json.loads(source_access.stored('rbx/object-backup/source-reader'))
    if source.get('scope_qualified') is not True:raise ValueError('source_scope_not_qualified')
    owner=json.loads(source_access.stored('rbx/object-backup/memory-custody'))
    broad=source_access.stored(owner['source']['source_access_ref']).decode().splitlines()[0]
    if source['access_key']==broad:raise ValueError('owner_source_key_refused')
    config=json.loads((stage/'local/runtime.json').read_text())
    material={'source':base64.b64encode(json.dumps(source).encode()).decode()}
    for role in ('writer','verifier'):
        raw=source_access.stored('rbx/object-backup/b2-'+role)
        access.admit(role,json.loads(raw),config['bucket_id'])
        material[role]=base64.b64encode(raw).decode()
    for name,ref in [('signer','rbx/object-backup/memory-signer'),
                     ('capture-ping','rbx/healthchecks/memory-capture'),
                     ('witness-ping','rbx/healthchecks/memory-witness')]:
        material[name]=base64.b64encode(source_access.stored(ref).strip()).decode()
    subprocess.run(['sudo','-v'],check=True)
    command=['sudo','-n','/usr/bin/python3','-B',str(Path(__file__).with_name('activate-root.py').resolve()),
             '--public-stage',str(stage)]
    return subprocess.run(command,input=json.dumps(material).encode(),timeout=1000).returncode


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as e:
        print(json.dumps({'activated':False,'error_type':type(e).__name__}));raise SystemExit(1)
