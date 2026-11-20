#!/usr/bin/env python3
"""Prepare/install only the archive witness; never install source or writer access."""
import argparse,base64,hashlib,json,os,re,resource,subprocess,sys,tempfile
from pathlib import Path
import access,source_access,supervised

HERE=Path(__file__).resolve().parent
FILES={'runtime.py','access.py','b2_wire.py','clock_status.py','workload-public.txt','runtime.json'}
NAMES={'verifier','witness-ping'}


def validate_stage(stage,expected):
    stage=Path(stage)
    if not stage.is_absolute() or stage.resolve()!=stage:raise ValueError('unsafe_witness_stage')
    raw=(stage/'manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('witness_manifest_drift')
    manifest=json.loads(raw)
    wanted={'files/'+x for x in FILES}|{'witness.service','witness.timer'}
    actual={str(p.relative_to(stage)) for p in stage.rglob('*') if p.is_file() and p.name!='manifest.json'}
    if set(manifest)!=wanted or actual!=wanted or any(p.is_symlink() for p in stage.rglob('*')):
        raise ValueError('unexpected_witness_members')
    for name,digest in manifest.items():
        p=stage/name
        if not p.resolve().is_relative_to(stage) or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:
            raise ValueError('witness_stage_drift')
    config=json.loads((stage/'files/runtime.json').read_text())
    if (config.get('dataset_id')!='C008' or config.get('source_scope_qualified') is not False
        or config.get('capture_enabled') is not False or config.get('fresh_restore_qualified') is not True):
        raise ValueError('witness_only_qualification_required')
    return config


def prepare(stage,qualification):
    q=supervised.trusted_receipt(qualification)
    if q.get('isolated_restore_passed') is not True or q.get('independent_readback_passed') is not True:
        raise ValueError('fresh_recovery_qualification_required')
    stage=Path(stage)
    if not stage.is_absolute() or stage.resolve()!=stage or stage.exists():raise ValueError('new_witness_stage_required')
    custody=json.loads(source_access.stored('rbx/object-backup/memory-custody'))
    stage.mkdir(mode=0o700,parents=True);(stage/'files').mkdir(mode=0o755)
    for name in FILES-{'runtime.json','clock_status.py'}:
        (stage/'files'/name).write_bytes((HERE/name).read_bytes())
    (stage/'files/clock_status.py').write_bytes((HERE.parent/'dns-renewal/clock_status.py').read_bytes())
    config={'dataset_id':'C008','bucket_id':custody['destination']['bucket_id'],
      'source_scope_qualified':False,'capture_enabled':False,'fresh_restore_qualified':True,
      'qualified_run_id':q['run_id'],'owner_private_key_deployed':False,'source_deletion_enabled':False}
    (stage/'files/runtime.json').write_text(json.dumps(config,indent=2)+'\n')
    (stage/'witness.service').write_text((HERE/'witness.service').read_text().replace('/opt/rbx-memory-backup','/opt/rbx-memory-witness'))
    (stage/'witness.timer').write_bytes((HERE/'witness.timer').read_bytes())
    manifest={str(p.relative_to(stage)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(stage.rglob('*')) if p.is_file()}
    raw=(json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode();(stage/'manifest.json').write_bytes(raw)
    digest=hashlib.sha256(raw).hexdigest();validate_stage(stage,digest)
    return {'witness_stage_prepared':True,'release_manifest_sha256':digest,
      'source_or_writer_credential_in_stage':False,'capture_scheduler_enabled':False,'installed':False}


def root_install(stage,digest):
    if os.geteuid()!=0:raise ValueError('owner_administrative_authentication_required')
    config=validate_stage(stage,digest)
    paths=['/opt/rbx-memory-witness','/etc/systemd/system/rbx-memory-witness.service',
      '/etc/systemd/system/rbx-memory-witness.timer','/etc/credstore.encrypted/rbx-memory-verifier',
      '/etc/credstore.encrypted/rbx-memory-witness-ping']
    if any(Path(p).exists() or Path(p).is_symlink() for p in paths):raise ValueError('existing_witness_requires_reviewed_update')
    raw=sys.stdin.buffer.read(65537)
    if len(raw)>65536:raise ValueError('oversized_witness_material')
    material=json.loads(raw)
    if set(material)!=NAMES:raise ValueError('unexpected_witness_credential_set')
    values={k:base64.b64decode(v,validate=True) for k,v in material.items()}
    access.admit('verifier',json.loads(values['verifier']),config['bucket_id'])
    if not re.fullmatch(rb'https://hc-ping\.com/[a-f0-9-]{36}',values['witness-ping']):raise ValueError('unexpected_witness_signal')
    with tempfile.TemporaryDirectory(prefix='rbx-witness-wrapped-') as temp:
        work=Path(temp)
        for name,value in values.items():
            credential='b2' if name=='verifier' else 'healthchecks'
            wrapped=subprocess.run(['/usr/bin/systemd-creds','encrypt','--with-key=host','--name='+credential,'-','-'],
                input=value,capture_output=True,check=True,timeout=20).stdout
            (work/(name+'.encrypted')).write_bytes(wrapped)
        variables=work/'vars.json';variables.write_text(json.dumps({'witness_stage':str(stage),
            'wrapped_credentials':str(work),'ansible_python_interpreter':'/usr/bin/python3'}))
        command=['/usr/bin/ansible-playbook','-i','localhost,',str(HERE/'install-witness.yml'),'-e','@'+str(variables)]
        for extra in (['--check','--diff'],[],['--check']):subprocess.run(command+extra,check=True,timeout=120)
    subprocess.run(['systemctl','daemon-reload'],check=True)
    subprocess.run(['systemctl','start','rbx-memory-witness.service'],check=True,timeout=200)
    subprocess.run(['systemctl','enable','--now','rbx-memory-witness.timer'],check=True)
    subprocess.run(['systemctl','is-active','--quiet','rbx-memory-witness.timer'],check=True)
    return {'witness_active':True,'first_readback_succeeded':True,'capture_scheduler_enabled':False,
      'source_writer_signer_or_owner_key_deployed':False,'natural_timer_execution_still_requires_observation':True}


def activate(stage,digest):
    config=validate_stage(stage,digest)
    verifier=source_access.stored('rbx/object-backup/b2-verifier')
    access.admit('verifier',json.loads(verifier),config['bucket_id'])
    ping=source_access.stored('rbx/healthchecks/memory-witness').strip()
    material={'verifier':base64.b64encode(verifier).decode(),'witness-ping':base64.b64encode(ping).decode()}
    subprocess.run(['sudo','-v'],check=True)
    subprocess.run(['sudo','-n','/usr/bin/python3','-B',str(Path(__file__).resolve()),'root-install',
      '--stage',str(stage),'--manifest-sha',digest],input=json.dumps(material).encode(),check=True,timeout=600)
    return {'protected_witness_installation_completed':True,'capture_scheduler_enabled':False}


def main():
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=['prepare','activate','root-install'])
    p.add_argument('--stage',required=True,type=Path);p.add_argument('--qualification',type=Path);p.add_argument('--manifest-sha')
    a=p.parse_args()
    result=prepare(a.stage,a.qualification) if a.mode=='prepare' else root_install(a.stage,a.manifest_sha) if a.mode=='root-install' else activate(a.stage,a.manifest_sha)
    print(json.dumps(result))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'completed':False,'error_type':type(error).__name__}));raise SystemExit(1)
