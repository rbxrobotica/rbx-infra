#!/usr/bin/env python3
"""Separate local HTTPS calibration and passive B2 storage qualification.

Neither stage makes B2 an admissible active backend. No local-backend apply,
lock bypass, real states, cloud resources managed by OpenTofu, or plaintext
state artifacts are used. The control receipt must be checkpointed in Git
before the storage stage is admitted.
"""
import argparse,json,os,signal,stat,subprocess,time,uuid
from pathlib import Path
import core,engine,reference
from qualify import read_object,versions,wait_marker


def checked_receipt(path,expected,binding):
    raw=path.read_bytes()
    if core.digest(raw)!=expected:raise ValueError('untrusted_control_receipt')
    value=json.loads(raw)
    if (value.get('type')!='local HTTPS calibration, not provider qualification'
        or value.get('run_id')!=binding['run_id'] or not value.get('completed')
        or not value.get('all_executed_gates_pass')):
        raise ValueError('incomplete_control_receipt')
    return value


def encrypted(payload):
    return (len(payload)<=65536 and 'encrypted_data' in json.loads(payload)
        and b'RBX_SYNTHETIC_TOFU_CANARY_V1' not in payload)


def remote_receipt_checkpoint(path,expected):
    location=subprocess.run(['git','-C',str(path.parent),'rev-parse','--show-toplevel'],capture_output=True,timeout=10)
    if location.returncode:raise ValueError('receipt_repository_missing')
    repo=Path(location.stdout.decode().strip());relative=str(path.relative_to(repo))
    result=subprocess.run(['git','-C',str(repo),'show',
        'refs/remotes/origin/codex/synthetic-tofu-backend-20261119:'+relative],capture_output=True,timeout=10)
    if result.returncode or core.digest(result.stdout)!=expected:raise ValueError('receipt_not_checkpointed_remotely')


def run(args):
    b=core.load();root=core.owned_dir(core.BASE/b['run_id']/args.output.stem)
    report={'type':('local HTTPS calibration, not provider qualification' if args.stage=='control'
       else 'passive B2 storage qualification, not active backend admission'),
       'run_id':b['run_id'],'started_at_utc':core.utc(),'observations':{},'gates':{},
       'failures':[],'source_states_migrated':False,'real_resources_managed':False,
       'native_b2_locking_failure_waived':False,'source_sha256':{
          p.name:core.digest(p.read_bytes()) for p in core.HERE.glob('*.py')}}
    out=report['observations'];g=report['gates'];children=[];control=None
    def checkpoint():
        fd=os.open(args.output,os.O_WRONLY|os.O_TRUNC|os.O_NOFOLLOW)
        with os.fdopen(fd,'w') as stream:stream.write(json.dumps(report,indent=2,sort_keys=True)+'\n')
    def command(working,argv,label,generation=1,crypto='correct',hold=False,no_cloud=False,env=None):
        env=env or engine.environment(b,working,generation,crypto=crypto,hold=hold)
        if no_cloud:
            env={k:v for k,v in env.items() if not k.startswith('AWS_')}
            env['AWS_EC2_METADATA_DISABLED']='true'
        result,raw,_=engine.run(args.tofu,working,env,argv);out[label]=result;checkpoint()
        return result,raw
    def generation(working,path,expected,label,crypto='correct'):
        result,raw=command(working,['show','-json',str(path)],label,expected,crypto,no_cloud=True)
        value=json.loads(raw) if result['exit_code']==0 else {}
        actual=value.get('values',{}).get('outputs',{}).get('generation',{}).get('value')
        out[label]['expected_generation_matches']=actual==expected
        return result['exit_code']==0 and actual==expected
    try:
        if args.stage=='control':
            control=reference.Control(root/'https');a=control.configure(b,'a');bw=control.configure(b,'b')
            for label,working in [('a',a),('b',bw)]:
                result,_=command(working,['init','-input=false','-no-color','-reconfigure'],'init_'+label,no_cloud=True)
                if result['exit_code']:raise ValueError('control_initialization_failed')
            # Explicit plan before every calibration apply; only terraform_data is managed.
            result,_=command(a,['plan','-input=false','-no-color','-lock-timeout=2s'],'plan_generation1',no_cloud=True)
            if result['exit_code']:raise ValueError('control_plan_failed')
            result,_=command(a,['apply','-input=false','-no-color','-auto-approve','-lock-timeout=2s'],'seed',no_cloud=True)
            if result['exit_code']:raise ValueError('control_seed_failed')
            snapshots={1:control.store.state}
            g['encrypted_generation1']=encrypted(snapshots[1])
            result,_=command(a,['plan','-input=false','-no-color','-lock-timeout=2s'],'plan_generation2',2,no_cloud=True)
            if result['exit_code']:raise ValueError('control_plan_failed')
            env=engine.environment(b,a,2,hold=True);env={k:v for k,v in env.items() if not k.startswith('AWS_')}
            child=engine.start(args.tofu,a,env,['apply','-input=false','-no-color','-auto-approve','-lock-timeout=2s']);children.append(child)
            if not wait_marker(a/'gate/ready',child):
                out['held_apply']=engine.finish(child)[0];raise ValueError('control_hold_failed')
            owner_id=control.store.lock['ID'];out['known_lock_owner_id']=owner_id
            result,_=command(bw,['force-unlock','-force',str(uuid.uuid4())],'wrong_owner_unlock',no_cloud=True)
            g['wrong_owner_unlock_refused']=result['exit_code']!=0 and control.store.lock['ID']==owner_id
            result,_=command(bw,['plan','-input=false','-no-color','-lock-timeout=2s'],'contender',3,no_cloud=True)
            g['two_engine_exclusion']=result['exit_code']!=0 and result['lock_error'] and control.store.lock['ID']==owner_id
            (a/'gate/release').write_text('release known synthetic owner\n')
            out['held_apply']=engine.finish(child,30)[0]
            if out['held_apply']['exit_code']:raise ValueError('control_owner_apply_failed')
            snapshots[2]=control.store.state;g['encrypted_generation2']=encrypted(snapshots[2])
            g['owner_release']=control.store.lock is None
            result,_=command(bw,['plan','-input=false','-no-color','-lock-timeout=2s'],'reacquire_after_release',2,no_cloud=True)
            g['reacquire_after_release']=result['exit_code']==0 and control.store.lock is None
            out['https']={'bound_to_loopback':True,'tls_verification':True,'cloud_credentials_in_control':False,
                          'closed_before_offline_reads':True,'not_a_production_backend':True}
            out['control_events']=control.store.events
            control.close();control=None
            (root/'https/control-key.pem').unlink()
            out['snapshots']={}
            for number,payload in snapshots.items():
                path=root/('generation'+str(number)+'.enc');core.private_write(path,payload)
                out['snapshots'][str(number)]={'path':str(path),'sha256':core.digest(payload),'bytes':len(payload)}
                g['closed_control_offline_read_'+str(number)]=generation(a,path,number,'offline_generation'+str(number))
            g['distinct_encrypted_generations']=snapshots[1]!=snapshots[2]
        else:
            control_report=checked_receipt(args.control_receipt,args.control_sha256,b)
            remote_receipt_checkpoint(args.control_receipt,args.control_sha256)
            out['trusted_control_receipt_sha256']=args.control_sha256
            s3=core.client(b,'runtime',args.sdk_dir);reader=core.client(b,'reader',args.sdk_dir)
            owner=core.client(b,'setup-lab',args.sdk_dir);archive=core.client(b,'archive-reader',args.sdk_dir)
            bucket=b['buckets']['state']['bucketName'];ab=b['buckets']['archive']['bucketName']
            key=b['prefix']+'active/storage-only/state.enc';total=0
            def put(client,bucket,key,payload):
                nonlocal total
                total+=len(payload)
                if len(payload)>65536 or total>1024*1024:raise ValueError('payload_budget_exceeded')
                return client.put_object(Bucket=bucket,Key=key,Body=payload,ServerSideEncryption='AES256')
            history=versions(reader,b,key)
            if history['versions'] or history['markers']:raise ValueError('preserve_existing_storage_attempt')
            snapshots={};meta={};out['snapshots']={}
            for number in (1,2):
                entry=control_report['observations']['snapshots'][str(number)];path=Path(entry['path'])
                expected=core.BASE/b['run_id']/args.control_receipt.stem/('generation'+str(number)+'.enc')
                if (path!=expected or path.resolve()!=path or not stat.S_ISREG(path.lstat().st_mode)
                    or path.stat().st_uid!=os.getuid() or path.stat().st_mode&0o077):raise ValueError('unexpected_cipher_path')
                payload=path.read_bytes()
                if core.digest(payload)!=entry['sha256'] or not encrypted(payload):raise ValueError('untrusted_cipher_payload')
                made=put(s3,bucket,key,payload)
                got,metadata=read_object(reader,bucket,key,made['VersionId']);meta[number]=metadata
                g['storage_cipher_readback_'+str(number)]=got==payload and metadata['sse']=='AES256'
                archive_key=b['prefix']+'snapshots/storage-control-generation'+str(number)+'.enc'
                made=put(owner,ab,archive_key,payload);again,am=read_object(archive,ab,archive_key,made['VersionId'])
                retained=archive.get_object_retention(Bucket=ab,Key=archive_key,VersionId=made['VersionId'])['Retention']
                am['retention']={'mode':retained['Mode'],'retain_until':str(retained['RetainUntilDate'])}
                out['snapshots'][str(number)]={'mutable':metadata,'archive':am}
                g['compliance_archive_'+str(number)]=(again==payload and retained['Mode']=='COMPLIANCE' and retained['RetainUntilDate'].timestamp()>time.time())
                snapshots[number]=payload;checkpoint()
            previous,pm=read_object(reader,bucket,key,meta[1]['version_id'])
            out['version_history']=versions(reader,b,key)
            g['versioned_old_state_recovery']=previous==snapshots[1] and meta[1]['version_id']!=meta[2]['version_id']
            outside=b['prefix']+'excluded/storage-canary';put(owner,bucket,outside,b'owned-prefix-canary')
            denied,_=core.safe_call(s3.get_object,Bucket=bucket,Key=outside);out['outside_prefix']=denied
            cross=b['prefix']+'active/storage-cross-bucket-canary';made=put(owner,ab,cross,b'owned-bucket-canary')
            denied2,_=core.safe_call(s3.get_object,Bucket=ab,Key=cross);out['outside_bucket']=denied2
            locked,_=core.safe_call(owner.delete_object,Bucket=ab,Key=cross,VersionId=made['VersionId']);out['compliance_delete_canary']=locked
            vkey=b['prefix']+'active/probes/storage-version-delete';made=put(owner,bucket,vkey,b'owned-version-delete-canary')
            denied3,_=core.safe_call(s3.delete_object,Bucket=bucket,Key=vkey,VersionId=made['VersionId']);out['runtime_version_delete']=denied3
            g['least_privilege']=all(x.get('http') in (401,403) for x in (denied,denied2,denied3))
            g['compliance_denies_capable_identity_delete']=locked.get('http') in (401,403)
            # All remote paths and commands below are fixed; only encrypted bytes cross SSH.
            ssh=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','StrictHostKeyChecking=yes',
                 '-o','ForwardAgent=no','-o','ClearAllForwardings=yes','thinkcentre']
            remote_base='/home/psyctl/.local/share/rbx-agent-handoffs/tofun19'
            meta_safe={'run_id':b['run_id'],'control_receipt_sha256':args.control_sha256,
                       'snapshots':{str(n):{'sha256':core.digest(x),'bytes':len(x)} for n,x in snapshots.items()}}
            remote_script="import os,pathlib,sys; p=pathlib.Path('"+remote_base+"'); assert p.parent.resolve()==p.parent; p.mkdir(mode=0o700,exist_ok=False); os.chmod(p,0o700)"
            started=time.monotonic();r=subprocess.run(ssh+['python3 -c '+__import__('shlex').quote(remote_script)],capture_output=True,timeout=20)
            if r.returncode:raise ValueError('independent_copy_directory_unavailable')
            out['independent_copy']={'host':'ThinkCentre','path':remote_base,'same_lan_limit':True,'files':{}}
            for filename,payload in [('manifest.json',json.dumps(meta_safe,sort_keys=True).encode()),
                                      ('generation1.enc',snapshots[1]),('generation2.enc',snapshots[2])]:
                remote=remote_base+'/'+filename
                script="import os,sys; f=os.open('"+remote+"',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600); s=os.fdopen(f,'wb'); s.write(sys.stdin.buffer.read(65537)); s.flush(); os.fsync(s.fileno()); s.close()"
                r=subprocess.run(ssh+['python3 -c '+__import__('shlex').quote(script)],input=payload,capture_output=True,timeout=20)
                if r.returncode:raise ValueError('independent_copy_write_failed')
                read_script="import os,sys; f=os.open('"+remote+"',os.O_RDONLY|os.O_NOFOLLOW); s=os.fdopen(f,'rb'); sys.stdout.buffer.write(s.read(65537))"
                r=subprocess.run(ssh+['python3 -c '+__import__('shlex').quote(read_script)],capture_output=True,timeout=20)
                if r.returncode or r.stdout!=payload:raise ValueError('independent_copy_readback_failed')
                if filename.endswith('.enc'):core.private_write(root/filename,r.stdout)
                out['independent_copy']['files'][filename]={'sha256':core.digest(r.stdout),'bytes':len(r.stdout)}
            out['independent_copy']['copy_and_readback_seconds']=round(time.monotonic()-started,4)
            g['independent_host_cipher_readback']=True;checkpoint()
            # Read-only show of explicit encrypted artifacts; no backend initialization or fallback.
            offline=engine.configure(b,root,'offline')
            started=time.monotonic()
            for number in (1,2):g['offline_recovered_generation_'+str(number)]=generation(offline,root/('generation'+str(number)+'.enc'),number,'restored_'+str(number))
            out['offline_functional_read_seconds']=round(time.monotonic()-started,4)
            for mode in ('wrong','missing'):
                result,_=command(offline,['show','-json',str(root/'generation1.enc')],'reject_'+mode,crypto=mode,no_cloud=True)
                g['reject_'+mode+'_key']=result['exit_code']!=0 and result['encryption_error']
            corrupt=root/'corrupt.enc';core.private_write(corrupt,snapshots[1][:len(snapshots[1])//2])
            result,_=command(offline,['show','-json',str(corrupt)],'reject_corruption',no_cloud=True)
            g['reject_corruption']=result['exit_code']!=0 and (result['encryption_error'] or result['artifact_error'])
            try:checked_receipt(args.control_receipt,'0'*64,b)
            except ValueError:g['reject_untrusted_manifest']=True
            else:g['reject_untrusted_manifest']=False
            before=versions(reader,b,key)
            unavailable=engine.configure(b,root,'unavailable','https://127.0.0.1:1')
            init,_=command(unavailable,['init','-input=false','-no-color','-reconfigure'],'unavailable_init')
            blocked,_=command(unavailable,['apply','-input=false','-no-color','-auto-approve','-lock-timeout=1s'],'unavailable_apply')
            g['unavailable_fails_closed']=(init['exit_code']!=0 and init['backend_error'] and blocked['exit_code']!=0
                and blocked['backend_error'] and not list(unavailable.glob('*.tfstate*')) and not (unavailable/'gate/ready').exists())
            d=engine.configure(b,root,'denied');env=engine.environment(b,d,1,bad_credentials=True)
            invalid,_=command(d,['init','-input=false','-no-color','-reconfigure'],'invalid_credentials_init',env=env)
            g['denied_fails_closed']=invalid['exit_code']!=0 and invalid['backend_error'] and not list(d.glob('*.tfstate*'))
            a=engine.configure(b,core.BASE/b['run_id'],'a');bw=engine.configure(b,core.BASE/b['run_id'],'b')
            # Two fresh native acquisition attempts: fail safe rather than bypassing B2's failed lock.
            processes=[]
            for w in (a,bw):
                child=engine.start(args.tofu,w,engine.environment(b,w,1),['plan','-input=false','-no-color','-lock-timeout=1s'])
                processes.append(child);children.append(child)
            out['native_parallel_plans']=[engine.finish(p)[0] for p in processes]
            g['native_failed_lock_prevents_both_plans']=all(x['exit_code']!=0 and x['lock_error'] for x in out['native_parallel_plans'])
            g['failed_operations_preserve_storage_history']=versions(reader,b,key)==before
            native_key=b['prefix']+'active/state.tfstate'
            out['native_state_history']=versions(reader,b,native_key)
            g['no_native_state_written']=not out['native_state_history']['versions']
            out['uploaded_payload_bytes_this_stage']=total
            out['payload_budget_bytes']=1024*1024;out['independent_receipt_anchored_in_git']=True
        report['completed']=True
    except Exception as e:
        report['completed']=False
        report['failures'].append({'error_type':type(e).__name__,
            'reason':str(e) if isinstance(e,ValueError) and str(e).replace('_','').isalnum() else 'stage_unavailable'})
    finally:
        for child in children:
            if child.poll() is None:
                child.send_signal(signal.SIGINT)
                try:engine.finish(child,10)
                except Exception:report['failures'].append({'reason':'owned_child_cleanup_requires_review'})
        if control is not None:control.close()
        report['all_executed_gates_pass']=bool(g) and all(g.values())
        report['active_backend_qualified']=False
        report['finished_at_utc']=core.utc();checkpoint()
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('stage',choices=('control','storage'))
    p.add_argument('--sdk-dir',type=Path);p.add_argument('--tofu',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--control-receipt',type=Path)
    p.add_argument('--control-sha256');args=p.parse_args()
    if args.output.parent.resolve()!=args.output.parent:raise ValueError('noncanonical_evidence_directory')
    if args.stage=='storage' and (not args.sdk_dir or not args.control_receipt or not args.control_sha256):
        raise ValueError('storage_preflight_missing')
    fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.close(fd)
    report=run(args)
    print(json.dumps({k:report[k] for k in ('type','completed','active_backend_qualified','gates','failures')}))
    if not report['completed'] or not report['all_executed_gates_pass']:raise SystemExit(2)


if __name__=='__main__':
    try:main()
    except Exception as e:
        print(json.dumps({'completed':False,'error_type':type(e).__name__}));raise SystemExit(1)
