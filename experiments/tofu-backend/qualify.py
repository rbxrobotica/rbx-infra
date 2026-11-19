#!/usr/bin/env python3
"""Primary-operated real qualification of one declared synthetic B2 fixture."""
import argparse,datetime as dt,json,os,secrets,shlex,signal,subprocess,time,uuid
from pathlib import Path
import core,engine


def versions(s3,binding,key):
    r=s3.list_object_versions(Bucket=binding['buckets']['state']['bucketName'],Prefix=key,MaxKeys=100)
    if r.get('IsTruncated'):raise ValueError('history_bound')
    return {'versions':[{'id':v['VersionId'],'latest':v['IsLatest'],'bytes':v['Size']} for v in r.get('Versions',[]) if v['Key']==key],
            'markers':[{'id':v['VersionId'],'latest':v['IsLatest']} for v in r.get('DeleteMarkers',[]) if v['Key']==key]}


def read_object(s3,bucket,key,version=None):
    args={'Bucket':bucket,'Key':key}
    if version:args['VersionId']=version
    response=s3.get_object(**args);body=response['Body'].read(65537)
    if len(body)>65536:raise ValueError('synthetic_object_bound')
    return body,{'version_id':response['VersionId'],'sha256':core.digest(body),'bytes':len(body),
      'sse':response.get('ServerSideEncryption'),'retention_mode':response.get('ObjectLockMode'),
      'retain_until':str(response.get('ObjectLockRetainUntilDate'))}


def wait_marker(path,process,seconds=25):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if path.exists():return True
        if process.poll() is not None:return False
        time.sleep(.1)
    return False


def run(args):
    b=core.load();root=core.owned_dir(core.BASE/b['run_id']);s3=core.client(b,'runtime',args.sdk_dir)
    reader=core.client(b,'reader',args.sdk_dir);owner=core.client(b,'setup-lab',args.sdk_dir)
    archive_reader=core.client(b,'archive-reader',args.sdk_dir)
    bucket=b['buckets']['state']['bucketName'];archive_bucket=b['buckets']['archive']['bucketName']
    key=b['prefix']+'active/state.tfstate';lock=key+'.tflock'
    report={'type':'executed synthetic backend qualification','started_at_utc':core.utc(),
       'run_id':b['run_id'],'provider':'Backblaze B2','engine_version':core.PINS['version'],
       'binary_sha256':engine.BINARY_SHA256,'endpoint':b['endpoint'],'source_states_migrated':False,
       'real_resources_managed':False,'admin_key_in_engine':False,'observations':{},'gates':{},'failures':[]}
    out=report['observations'];g=report['gates'];children=[]
    def checkpoint():
        args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    def command(working,env,argv,label):
        summary,stdout,stderr=engine.run(args.tofu,working,env,argv);out[label]=summary;checkpoint()
        return summary,stdout
    try:
        # Raw compare-and-create is tested on a separate, harmless owned canary.
        probe=b['prefix']+'active/probes/conditional-'+args.output.stem
        first,_=core.safe_call(s3.put_object,Bucket=bucket,Key=probe,Body=b'owned-synthetic-first',IfNoneMatch='*',ServerSideEncryption='AES256')
        second,_=core.safe_call(s3.put_object,Bucket=bucket,Key=probe,Body=b'owned-synthetic-second',IfNoneMatch='*',ServerSideEncryption='AES256')
        current=b'';metadata,found=core.safe_call(reader.get_object,Bucket=bucket,Key=probe)
        if found is not None:
            current=found['Body'].read(65537)
            if len(current)>65536:raise ValueError('probe_body_bound')
        out['conditional_put']={'first':first,'conflict':second,'current':metadata,
           'first_body_preserved':current==b'owned-synthetic-first'}
        g['atomic_conditional_put']=first['ok'] and second.get('http')==412 and current==b'owned-synthetic-first';checkpoint()
        a=engine.configure(b,root,'a');bw=engine.configure(b,root,'b')
        for name,working in [('a',a),('b',bw)]:
            r,_=command(working,engine.environment(b,working,1),['init','-input=false','-no-color','-reconfigure'],'init_'+name)
            if r['exit_code']:raise ValueError('engine_backend_initialization_failed')
        seed,_=command(a,engine.environment(b,a,1),['apply','-input=false','-no-color','-auto-approve','-lock-timeout=2s'],'seed_apply')
        if seed['exit_code']:raise ValueError('seed_apply_failed')
        cipher1,meta1=read_object(reader,bucket,key);core.private_write(root/'generation1.enc',cipher1)
        out['generation1']=meta1
        encrypted=json.loads(cipher1)
        g['client_side_encryption']=('encrypted_data' in encrypted and b'RBX_SYNTHETIC_TOFU_CANARY_V1' not in cipher1)
        g['server_side_encryption']=meta1['sse']=='AES256';checkpoint()
        # Keep both engines alive only inside a bounded, synthetic provisioner gate.
        for working in (a,bw):
            for marker in ('ready','release'):(working/'gate'/marker).unlink(missing_ok=True)
        proc_a=engine.start(args.tofu,a,engine.environment(b,a,2,hold=True),
              ['apply','-input=false','-no-color','-auto-approve','-lock-timeout=2s']);children.append(proc_a)
        ready_a=wait_marker(a/'gate/ready',proc_a)
        out['contender_a_ready']=ready_a
        if not ready_a:
            out['contender_a']=engine.finish(proc_a)[0];raise ValueError('first_contender_not_held')
        held_raw,held_meta=read_object(reader,bucket,lock);held=json.loads(held_raw)
        out['held_lock']={'id':held['ID'],'operation':held.get('Operation'),'version':held_meta}
        wrong,_=command(bw,engine.environment(b,bw,2),['force-unlock','-force',str(uuid.uuid4())],'wrong_id_unlock')
        same,_=read_object(reader,bucket,lock)
        g['wrong_id_unlock_refused']=wrong['exit_code']!=0 and json.loads(same)['ID']==held['ID']
        proc_b=engine.start(args.tofu,bw,engine.environment(b,bw,3,hold=True),
              ['apply','-input=false','-no-color','-auto-approve','-lock-timeout=2s']);children.append(proc_b)
        ready_b=wait_marker(bw/'gate/ready',proc_b,8)
        out['contender_b_ready']=ready_b
        if ready_b:
            changed,changed_meta=read_object(reader,bucket,lock)
            out['overlapping_lock']={'id':json.loads(changed)['ID'],'version':changed_meta}
            proc_b.send_signal(signal.SIGINT);out['contender_b']=engine.finish(proc_b,12)[0]
            proc_a.send_signal(signal.SIGINT);out['contender_a']=engine.finish(proc_a,12)[0]
            g['two_engine_exclusion']=False
        else:
            result_b=engine.finish(proc_b,15)[0];out['contender_b']=result_b
            still,still_meta=read_object(reader,bucket,lock)
            g['two_engine_exclusion']=(result_b['exit_code']!=0 and result_b['lock_error']
                and json.loads(still)['ID']==held['ID'] and still_meta['version_id']==held_meta['version_id'])
            (a/'gate/release').write_text('release owned synthetic provisioner\n')
            result_a=engine.finish(proc_a,30)[0];out['contender_a']=result_a
            g['normal_owner_completion']=result_a['exit_code']==0
        missing,_=core.safe_call(reader.get_object,Bucket=bucket,Key=lock)
        out['post_release_lock']=missing;g['lock_release_and_reacquire']=missing.get('http')==404;checkpoint()
        # A clean sequential apply follows the bounded contention experiment, even if exclusion failed.
        seq,_=command(a,engine.environment(b,a,4),['apply','-input=false','-no-color','-auto-approve','-lock-timeout=2s'],'sequential_generation4')
        if seq['exit_code']:raise ValueError('sequential_reacquisition_failed')
        g['lock_release_and_reacquire'] &= True
        cipher2,meta2=read_object(reader,bucket,key);core.private_write(root/'generation4.enc',cipher2)
        old,old_meta=read_object(reader,bucket,key,meta1['version_id'])
        out['generation4']=meta2;out['state_history']=versions(reader,b,key);out['lock_history']=versions(reader,b,lock)
        g['versioned_history']=(meta2['version_id']!=meta1['version_id'] and old==cipher1)
        out['old_version_readback']=old_meta;checkpoint()
        # Close two encrypted snapshots in a separately retained, scoped archive bucket.
        for gen,payload,metadata in [(1,cipher1,meta1),(4,cipher2,meta2)]:
            archive_key=b['prefix']+'snapshots/generation'+str(gen)+'.enc'
            made=owner.put_object(Bucket=archive_bucket,Key=archive_key,Body=payload,ServerSideEncryption='AES256')
            recovered,got=read_object(archive_reader,archive_bucket,archive_key,made['VersionId'])
            out['archive_generation'+str(gen)]=got
            retention=archive_reader.get_object_retention(Bucket=archive_bucket,Key=archive_key,VersionId=made['VersionId'])['Retention']
            out['archive_generation'+str(gen)]['retention']={'mode':retention['Mode'],'retain_until':str(retention['RetainUntilDate'])}
            g['retained_archive_generation'+str(gen)]=(recovered==payload and retention['Mode']=='COMPLIANCE'
                and retention['RetainUntilDate'].timestamp()>time.time())
        # All permission/destructive probes are separate reproducible canaries.
        outside=b['prefix']+'excluded/canary';owner.put_object(Bucket=bucket,Key=outside,Body=b'owned-outside-runtime-prefix',ServerSideEncryption='AES256')
        denied,_=core.safe_call(s3.get_object,Bucket=bucket,Key=outside);out['outside_prefix_denial']=denied
        cross=b['prefix']+'active/cross-bucket-probe';x=owner.put_object(Bucket=archive_bucket,Key=cross,Body=b'owned-cross-bucket-canary',ServerSideEncryption='AES256')
        denied2,_=core.safe_call(s3.get_object,Bucket=archive_bucket,Key=cross);out['outside_bucket_denial']=denied2
        retained_denial,_=core.safe_call(owner.delete_object,Bucket=archive_bucket,Key=cross,VersionId=x['VersionId']);out['retained_canary_delete_denial']=retained_denial
        version_probe=b['prefix']+'active/probes/version-delete';v=owner.put_object(Bucket=bucket,Key=version_probe,Body=b'owned-version-delete-canary',ServerSideEncryption='AES256')
        denied3,_=core.safe_call(s3.delete_object,Bucket=bucket,Key=version_probe,VersionId=v['VersionId']);out['runtime_version_delete_denial']=denied3
        g['least_privilege']=(all(q.get('http') in (401,403) for q in (denied,denied2,denied3)) and not retained_denial['ok'])
        # Missing/wrong encryption material cannot read backend data or create replacement state.
        before=versions(reader,b,key)
        for mode in ('missing','wrong'):
            failed,_=command(bw,engine.environment(b,bw,4,crypto=mode),['show','-json'],'encryption_'+mode)
            g['reject_'+mode+'_key']=failed['exit_code']!=0
        g['failed_key_reads_preserve_history']=versions(reader,b,key)==before
        # The unavailable case uses a separate fixture dir, not an automatic fallback.
        failed_dir=engine.configure(b,root,'unavailable','https://127.0.0.1:1')
        unavailable,_=command(failed_dir,engine.environment(b,failed_dir,8),['init','-input=false','-no-color','-reconfigure'],'unavailable_init')
        blocked,_=command(failed_dir,engine.environment(b,failed_dir,8),['apply','-input=false','-no-color','-auto-approve','-lock-timeout=1s'],'unavailable_apply')
        g['backend_unavailable_fail_closed']=(unavailable['exit_code']!=0 and blocked['exit_code']!=0
           and not (failed_dir/'terraform.tfstate').exists() and not (failed_dir/'gate/ready').exists())
        denied_dir=engine.configure(b,root,'denied')
        invalid,_=command(denied_dir,engine.environment(b,denied_dir,8,bad_credentials=True),['init','-input=false','-no-color','-reconfigure'],'invalid_credentials_init')
        g['backend_denial_fail_closed']=invalid['exit_code']!=0 and not (denied_dir/'terraform.tfstate').exists()
        g['failed_backend_operations_preserve_history']=versions(reader,b,key)==before
        # Offline recovery is explicit read-only artifact verification, never a local-backend apply.
        offline=engine.configure(b,root,'offline')
        for label,payload,expect in [('old',old,1),('current',cipher2,4)]:
            path=root/('recover-'+label+'.enc');core.private_write(path,payload)
            shown,raw=command(offline,engine.environment(b,offline,expect),['show','-json',str(path)],'offline_'+label)
            value=json.loads(raw) if shown['exit_code']==0 else {}
            actual=value.get('values',{}).get('outputs',{}).get('generation',{}).get('value')
            out['offline_'+label]['expected_generation_matches']=actual==expect
            g['offline_recovery_'+label]=shown['exit_code']==0 and actual==expect
        corrupt=root/'corrupt.enc';core.private_write(corrupt,cipher1[:len(cipher1)//2])
        broken,_=command(offline,engine.environment(b,offline,1),['show','-json',str(corrupt)],'corrupt_archive')
        g['corrupt_archive_rejected']=broken['exit_code']!=0
        report['completed_at_utc']=core.utc();report['completed']=True
    except Exception as e:
        report['failures'].append({'error_type':type(e).__name__,
           'reason':str(e) if isinstance(e,ValueError) and str(e).replace('_','').isalnum() else 'stage_unavailable'})
        report['completed']=False
    finally:
        for child in children:
            if child.poll() is None:
                child.send_signal(signal.SIGINT)
                try:engine.finish(child,10)
                except Exception:report['failures'].append({'reason':'owned_child_cleanup_requires_review'})
        report['all_executed_gates_pass']=bool(g) and all(g.values())
        report['qualification_passed']=report.get('completed',False) and report['all_executed_gates_pass']
        report['finished_at_utc']=core.utc();checkpoint()
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('stage',choices=('setup','run'))
    p.add_argument('--run-id');p.add_argument('--sdk-dir',type=Path);p.add_argument('--tofu',type=Path)
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    if args.output.exists():raise ValueError('preserve_previous_receipt')
    if args.stage=='setup':
        b=core.setup(args.run_id);report={'type':'synthetic fixture setup','created_at_utc':core.utc(),
          'run_id':b['run_id'],'endpoint':b['endpoint'],'buckets':b['buckets'],'prefix':b['prefix'],
          'setup_complete':b['setup_complete'],'role_caps':core.CAPS,'admin_in_engine':False,
          'private_custody_ref':str(core.PASS_STORE),'original_buckets_keys_states_changed':False}
        args.output.write_text(json.dumps(report,indent=2)+'\n')
    else:report=run(args)
    print(json.dumps({k:report[k] for k in ('run_id','setup_complete','completed','qualification_passed','gates','failures') if k in report},sort_keys=True))


if __name__=='__main__':
    try:main()
    except Exception as e:
        print(json.dumps({'completed':False,'error_type':type(e).__name__}));raise SystemExit(1)
