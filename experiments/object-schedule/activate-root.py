#!/usr/bin/env python3
"""Initial install only. Refuse preexisting state and enable after real service success."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import subprocess
import sys
import tempfile
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import access


def main():
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    p=argparse.ArgumentParser();p.add_argument('--public-stage',type=Path,required=True)
    args=p.parse_args();stage=args.public_stage.resolve();source=Path(__file__).resolve().parent
    if os.geteuid()!=0:raise ValueError('root_authentication_required')
    names=['source','writer','verifier','signer','capture-ping','witness-ping']
    paths=['/opt/rbx-memory-backup']+[
        '/etc/systemd/system/rbx-memory-'+x+'.'+kind for x in ('capture','witness') for kind in ('service','timer')
    ]+['/etc/credstore.encrypted/rbx-memory-'+x for x in names]
    if any(Path(x).exists() or Path(x).is_symlink() for x in paths):
        raise ValueError('existing_memory_install_requires_reviewed_update')
    manifest=json.loads((stage/'manifest.json').read_text())
    actual={str(p.relative_to(stage)) for p in stage.rglob('*') if p.is_file() and p.name!='manifest.json'}
    if actual!=set(manifest):raise ValueError('stage_member_mismatch')
    for name,sha in manifest.items():
        path=stage/name
        if path.is_symlink() or not path.resolve().is_relative_to(stage):raise ValueError('unsafe_release')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise ValueError('release_drift')
    config=json.loads((stage/'local/runtime.json').read_text())
    if config.get('source_scope_qualified') is not True or config.get('workload_restore_qualified') is not True:
        raise ValueError('live_qualification_required')
    raw=sys.stdin.buffer.read(65537)
    if len(raw)>65536:raise ValueError('oversized_material')
    payload=json.loads(raw)
    if set(payload)!=set(names):raise ValueError('unexpected_credential_set')
    values={k:base64.b64decode(v,validate=True) for k,v in payload.items()}
    reader=json.loads(values['source'])
    if reader.get('scope_qualified') is not True or reader.get('authentication_handoff_closed') is not True:
        raise ValueError('source_not_qualified')
    for role in ('writer','verifier'):access.admit(role,json.loads(values[role]),config['bucket_id'])
    private=base64.b64decode(values['signer'],validate=True)
    public=Ed25519PrivateKey.from_private_bytes(private).public_key().public_bytes(
        serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    if public!=base64.b64decode((stage/'local/workload-public.txt').read_bytes()):raise ValueError('signer_pin_mismatch')
    for role in ('capture-ping','witness-ping'):
        if not re.fullmatch(rb'https://hc-ping\.com/[a-f0-9-]{36}',values[role]):raise ValueError('signal_destination')
    with tempfile.TemporaryDirectory(prefix='rbx-memory-wrapped-') as temporary:
        work=Path(temporary)
        for name,value in values.items():
            credential_name='b2' if name in ('writer','verifier') else 'healthchecks' if name.endswith('-ping') else name
            wrapped=subprocess.run(['/usr/bin/systemd-creds','encrypt','--with-key=host','--name='+credential_name,'-','-'],
                input=value,capture_output=True,check=True,timeout=20).stdout
            (work/(name+'.encrypted')).write_bytes(wrapped)
        variables=work/'vars.json';variables.write_text(json.dumps({'public_stage':str(stage),
            'wrapped_credentials':str(work),'ansible_python_interpreter':'/usr/bin/python3'}))
        command=['/usr/bin/ansible-playbook','-i','localhost,',str(source/'install-local.yml'),'-e','@'+str(variables)]
        subprocess.run(command+['--check','--diff'],check=True,timeout=120)
        subprocess.run(command,check=True,timeout=120)
        subprocess.run(command+['--check'],check=True,timeout=120)
    subprocess.run(['systemctl','daemon-reload'],check=True)
    for role,timeout in [('capture',620),('witness',200)]:
        subprocess.run(['systemctl','start','rbx-memory-'+role+'.service'],check=True,timeout=timeout)
    subprocess.run(['systemctl','enable','--now','rbx-memory-capture.timer','rbx-memory-witness.timer'],check=True)
    for role in ('capture','witness'):
        subprocess.run(['systemctl','is-active','--quiet','rbx-memory-'+role+'.timer'],check=True)
    print(json.dumps({'activation_completed':True,'initial_capture_and_witness_succeeded':True,
        'timers_active':True,'natural_timer_executions_still_require_observation':True,
        'owner_gpg_and_admin_keys_deployed':False}))


if __name__=='__main__':
    try:main()
    except Exception as e:
        print(json.dumps({'activation_completed':False,'error_type':type(e).__name__}));raise SystemExit(1)
