#!/usr/bin/env python3
"""Add two single-bucket, one-day synthetic probe identities; no bucket edits."""
import argparse,json
from pathlib import Path
import core


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    if args.output.exists():raise ValueError('preserve_previous_receipt')
    b=core.load();auth=core.admin();report={'type':'single-bucket synthetic probe identity setup',
       'run_id':b['run_id'],'at_utc':core.utc(),'existing_keys_changed':False,'buckets_changed':False,'roles':{}}
    for role in ('state-probe','archive-probe'):
        target=core.PASS_STORE/(core.PASS_PREFIX+role+'.gpg')
        if target.exists():raise ValueError('existing_probe_identity_requires_review')
        buckets,prefix=core.scope(b,role);caps=core.CAPS[role]
        made=core.api(auth,'b2_create_key',{'accountId':b['account_id'],
          'keyName':'rbx-tofu-'+role+'-'+b['run_id'],'capabilities':caps,
          'bucketIds':buckets,'namePrefix':prefix,'validDurationInSeconds':86400})
        verified=core.wire().authorize(made['applicationKeyId'],made['applicationKey']);actual=core.wire().normalized(verified)
        if verified['accountId']!=b['account_id'] or set(actual['capabilities'])!=set(caps) or actual['bucketIds']!=buckets or actual['namePrefix']!=prefix:
            raise ValueError('single_bucket_probe_scope_mismatch')
        core.store(role,made)
        report['roles'][role]={'capabilities':caps,'bucketIds':buckets,'prefix':prefix,'ttl_seconds':86400,'encrypted_custody_verified':True}
    report['completed']=True
    with args.output.open('x') as f:json.dump(report,f,indent=2)
    print(json.dumps({'completed':True,'roles':list(report['roles'])}))


if __name__=='__main__':
    try:main()
    except Exception as e:
        print(json.dumps({'completed':False,'error_type':type(e).__name__}));raise SystemExit(1)
