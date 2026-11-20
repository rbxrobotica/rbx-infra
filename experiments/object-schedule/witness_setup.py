#!/usr/bin/env python3
"""Prepare/install only the archive witness; never install source or writer access."""
import argparse,base64,hashlib,json,os,re,resource,stat,subprocess,sys,tempfile,time
from pathlib import Path
import access,source_access,supervised

HERE=Path(__file__).resolve().parent
FILES={'runtime.py','access.py','access-plan.json','b2_wire.py','clock_status.py','workload-public.txt','runtime.json'}
NAMES={'verifier','witness-ping'}


def stage_snapshot(stage,expected):
    stage=Path(stage)
    if not stage.is_absolute() or stage.resolve()!=stage:raise ValueError('unsafe_witness_stage')
    raw=(stage/'manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('witness_manifest_drift')
    manifest=json.loads(raw)
    wanted={'files/'+x for x in FILES}|{'witness.service','witness.timer'}
    nodes={str(p.relative_to(stage)):p for p in stage.rglob('*')}
    if (set(manifest)!=wanted or set(nodes)!=wanted|{'files','manifest.json'}
        or not stat.S_ISDIR(nodes['files'].lstat().st_mode)
        or any(not stat.S_ISREG(nodes[n].lstat().st_mode) for n in wanted|{'manifest.json'})):
        raise ValueError('unexpected_witness_members')
    snapshot={}
    for name,digest in manifest.items():
        p=stage/name
        data=p.read_bytes()
        if not p.resolve().is_relative_to(stage) or hashlib.sha256(data).hexdigest()!=digest:
            raise ValueError('witness_stage_drift')
        snapshot[name]=data
    expected_unit=(HERE/'witness.service').read_text().replace('/opt/rbx-memory-backup','/opt/rbx-memory-witness').encode()
    if snapshot['witness.service']!=expected_unit or snapshot['witness.timer']!=(HERE/'witness.timer').read_bytes():
        raise ValueError('unreviewed_witness_units')
    config=json.loads(snapshot['files/runtime.json'])
    if (config.get('dataset_id')!='C008' or config.get('source_scope_qualified') is not False
        or config.get('capture_enabled') is not False or config.get('fresh_restore_qualified') is not True
        or not re.fullmatch(runtime_run_pattern(),config.get('qualified_run_id',''))
        or config.get('initial_expected_run_id')!=config.get('qualified_run_id')
        or not re.fullmatch('[a-f0-9]{64}',config.get('qualified_ciphertext_sha256',''))):
        raise ValueError('witness_only_qualification_required')
    return config,snapshot


def validate_stage(stage,expected):return stage_snapshot(stage,expected)[0]


def runtime_run_pattern():return r'\d{8}T\d{6}Z-[a-f0-9]{32}'


def verifier_material(raw,bucket_id):
    value=json.loads(raw);access.admit('verifier',value,bucket_id)
    fields={'applicationKeyId','applicationKey','capabilities','bucketIds','namePrefix'}
    result={k:value[k] for k in fields}
    return json.dumps(result).encode()


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
      'qualified_run_id':q['run_id'],'initial_expected_run_id':q['run_id'],
      'qualified_ciphertext_sha256':q['ciphertext_sha256'],
      'owner_private_key_deployed':False,'source_deletion_enabled':False}
    (stage/'files/runtime.json').write_text(json.dumps(config,indent=2)+'\n')
    (stage/'witness.service').write_text((HERE/'witness.service').read_text().replace('/opt/rbx-memory-backup','/opt/rbx-memory-witness'))
    (stage/'witness.timer').write_bytes((HERE/'witness.timer').read_bytes())
    manifest={str(p.relative_to(stage)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(stage.rglob('*')) if p.is_file()}
    raw=(json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode();(stage/'manifest.json').write_bytes(raw)
    digest=hashlib.sha256(raw).hexdigest();validate_stage(stage,digest)
    return {'witness_stage_prepared':True,'release_manifest_sha256':digest,
      'source_or_writer_credential_in_stage':False,'capture_scheduler_enabled':False,'installed':False}


def require_absent_units():
    for role in ('capture','witness'):
        for kind in ('service','timer'):
            unit='rbx-memory-'+role+'.'+kind
            result=subprocess.run(['systemctl','show',unit,'-p','LoadState'],capture_output=True,text=True,check=True,timeout=10)
            if result.stdout.strip()!='LoadState=not-found':raise ValueError('preexisting_memory_units_require_review')
            for base in ('/etc/systemd/system','/run/systemd/system','/usr/lib/systemd/system'):
                if (Path(base)/unit).exists() or (Path(base)/(unit+'.d')).exists():
                    raise ValueError('preexisting_memory_units_require_review')


def root_install(stage,digest,playbook_sha):
    if os.geteuid()!=0:raise ValueError('owner_administrative_authentication_required')
    config,snapshot=stage_snapshot(stage,digest)
    playbook=(HERE/'install-witness.yml').read_bytes()
    if hashlib.sha256(playbook).hexdigest()!=playbook_sha:raise ValueError('privileged_playbook_drift')
    require_absent_units()
    paths=['/opt/rbx-memory-witness','/etc/systemd/system/rbx-memory-witness.service',
      '/etc/systemd/system/rbx-memory-witness.timer','/etc/credstore.encrypted/rbx-memory-verifier',
      '/etc/credstore.encrypted/rbx-memory-witness-ping','/var/lib/rbx-memory-witness']
    if any(Path(p).exists() or Path(p).is_symlink() for p in paths):raise ValueError('existing_witness_requires_reviewed_update')
    raw=sys.stdin.buffer.read(65537)
    if len(raw)>65536:raise ValueError('oversized_witness_material')
    material=json.loads(raw)
    if set(material)!=NAMES:raise ValueError('unexpected_witness_credential_set')
    values={k:base64.b64decode(v,validate=True) for k,v in material.items()}
    values['verifier']=verifier_material(values['verifier'],config['bucket_id'])
    if not re.fullmatch(rb'https://hc-ping\.com/[a-f0-9-]{36}',values['witness-ping']):raise ValueError('unexpected_witness_signal')
    with tempfile.TemporaryDirectory(prefix='rbx-witness-wrapped-') as temp:
        work=Path(temp)
        # Ansible consumes only this verified root-owned snapshot, never the
        # caller's mutable stage after validation.
        frozen=work/'release';frozen.mkdir(mode=0o700);(frozen/'files').mkdir(mode=0o700)
        for name,data in snapshot.items():(frozen/name).write_bytes(data)
        reviewed_playbook=work/'install-witness.yml';reviewed_playbook.write_bytes(playbook)
        for name,value in values.items():
            credential='b2' if name=='verifier' else 'healthchecks'
            wrapped=subprocess.run(['/usr/bin/systemd-creds','encrypt','--with-key=host','--name='+credential,'-','-'],
                input=value,capture_output=True,check=True,timeout=20).stdout
            (work/(name+'.encrypted')).write_bytes(wrapped)
        variables=work/'vars.json';variables.write_text(json.dumps({'witness_stage':str(frozen),
            'witness_public_files':sorted(FILES),'wrapped_credentials':str(work),'ansible_python_interpreter':'/usr/bin/python3'}))
        command=['/usr/bin/ansible-playbook','-i','localhost,',str(reviewed_playbook),'-e','@'+str(variables)]
        for extra in (['--check','--diff'],[],['--check']):subprocess.run(command+extra,check=True,timeout=120)
    subprocess.run(['systemctl','daemon-reload'],check=True)
    started=time.time();subprocess.run(['systemctl','start','rbx-memory-witness.service'],check=True,timeout=200)
    success=Path('/var/lib/rbx-memory-witness/last-success.json')
    result=json.loads(success.read_bytes())
    if (success.stat().st_mtime<started or result.get('run_id')!=config['qualified_run_id']
        or result.get('ciphertext_sha256')!=config['qualified_ciphertext_sha256']):
        raise ValueError('first_witness_did_not_match_qualified_restore')
    subprocess.run(['systemctl','enable','--now','rbx-memory-witness.timer'],check=True)
    subprocess.run(['systemctl','is-active','--quiet','rbx-memory-witness.timer'],check=True)
    return {'witness_active':True,'first_readback_succeeded':True,'capture_scheduler_enabled':False,
      'source_writer_signer_or_owner_key_deployed':False,'natural_timer_execution_still_requires_observation':True}


def activate(stage,digest):
    config=validate_stage(stage,digest)
    verifier=verifier_material(source_access.stored('rbx/object-backup/b2-verifier'),config['bucket_id'])
    ping=source_access.stored('rbx/healthchecks/memory-witness').strip()
    material={'verifier':base64.b64encode(verifier).decode(),'witness-ping':base64.b64encode(ping).decode()}
    # Pin the privileged entry point/playbook to the reviewed published source.
    repo=HERE.parent.parent
    head=subprocess.run(['git','-C',str(repo),'rev-parse','HEAD'],capture_output=True,text=True,check=True,timeout=10).stdout.strip()
    advertised=subprocess.run(['git','-C',str(repo),'ls-remote','origin','refs/heads/'+supervised.BRANCH],capture_output=True,text=True,check=True,timeout=30).stdout.split()
    if not advertised or advertised[0]!=head:raise ValueError('installer_source_not_remote')
    for name in ('witness_setup.py','install-witness.yml'):
        pinned=subprocess.run(['git','-C',str(repo),'show',head+':experiments/object-schedule/'+name],capture_output=True,check=True,timeout=10).stdout
        if pinned!=(HERE/name).read_bytes():raise ValueError('privileged_source_drift')
    playbook_sha=hashlib.sha256((HERE/'install-witness.yml').read_bytes()).hexdigest()
    subprocess.run(['sudo','-v'],check=True)
    subprocess.run(['sudo','-n','/usr/bin/python3','-B',str(Path(__file__).resolve()),'root-install',
      '--stage',str(stage),'--manifest-sha',digest,'--playbook-sha',playbook_sha],input=json.dumps(material).encode(),check=True,timeout=600)
    return {'protected_witness_installation_completed':True,'capture_scheduler_enabled':False}


def main():
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=['prepare','activate','root-install'])
    p.add_argument('--stage',required=True,type=Path);p.add_argument('--qualification',type=Path);p.add_argument('--manifest-sha');p.add_argument('--playbook-sha')
    a=p.parse_args()
    result=prepare(a.stage,a.qualification) if a.mode=='prepare' else root_install(a.stage,a.manifest_sha,a.playbook_sha) if a.mode=='root-install' else activate(a.stage,a.manifest_sha)
    print(json.dumps(result))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'completed':False,'error_type':type(error).__name__}));raise SystemExit(1)
