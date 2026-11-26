#!/usr/bin/env python3
"""Native API setup for exact B2 capabilities; no account key in a worker."""
import argparse
import datetime as dt
import json
from pathlib import Path
import re
import subprocess
import time
import access
from b2_wire import ROOT, MAX_RESPONSE, NoRedirect, destination, request, authorize, normalized
ENTRIES={'writer':'rbx/object-backup/b2-writer','verifier':'rbx/object-backup/b2-verifier'}


def stored(entry):
    p=subprocess.run(['pass','show',entry],capture_output=True,timeout=30)
    if p.returncode:raise ValueError('custody_unavailable')
    return p.stdout


def provision(master_id,apply=False,transport=request):
    custody=json.loads(stored('rbx/object-backup/memory-custody'))
    bucket=custody['destination']['bucket_id']
    report={'scope':'two exact bucket/prefix-bound data-plane identities','apply_requested':apply,
            'prefix':access.PLAN['destination']['prefix'],'valid_days':90,'roles':[],
            'admin_deployed_to_runtime':False,'source_or_archive_data_mutation':False}
    for role,entry in ENTRIES.items():
        report['roles'].append({'role':role,'pass_ref':entry,'capabilities':access.desired(role,bucket)['capabilities']})
    if not apply:return report
    admin=authorize(master_id,stored('rbx/backblaze/setup-admin').decode().strip(),transport)
    if admin['accountId']!=master_id or 'writeKeys' not in normalized(admin)['capabilities']:
        raise ValueError('setup_identity_not_expected_account_admin')
    base=admin['apiInfo']['storageApi']['apiUrl'];token=admin['authorizationToken']
    for row in report['roles']:
        role=row['role'];entry=row['pass_ref'];created=None
        path=Path.home()/'.password-store'/(entry+'.gpg')
        if path.exists():
            value=json.loads(stored(entry));made=False
        else:
            payload={'accountId':admin['accountId'],'keyName':'rbx-memory-'+role+'-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ'),**access.desired(role,bucket)}
            value=transport(base+'/b2api/v4/b2_create_key',token,payload)
            created=value['applicationKeyId'];made=True
        try:
            access.admit(role,value,bucket)
            auth=authorize(value['applicationKeyId'],value['applicationKey'],transport)
            if auth['accountId']!=master_id:raise ValueError('scoped_key_wrong_account')
            access.admit(role,normalized(auth),bucket)
            expires=auth.get('applicationKeyExpirationTimestamp')
            if not isinstance(expires,int) or expires<1000*(time.time()+14*86400):
                raise ValueError('scoped_key_expiration_requires_rotation')
            if made:
                p=subprocess.run(['pass','insert','--multiline',entry],input=json.dumps(value).encode(),
                                 capture_output=True,timeout=45)
                if p.returncode:raise ValueError('scoped_custody_write_failed')
            if json.loads(stored(entry))!=value:raise ValueError('scoped_custody_readback_failed')
        except Exception:
            if created:
                # Roll back only the new key from this iteration, never an existing key or data.
                transport(base+'/b2api/v2/b2_delete_key',token,{'applicationKeyId':created})
            raise
        row.update(created=made,provider_scope_verified=True,encrypted_custody_verified=True,
                   expires_utc=dt.datetime.fromtimestamp(expires/1000,dt.timezone.utc).isoformat())
    subprocess.run(['git','-C',str(Path.home()/'.password-store'),'push','origin','HEAD'],
                   capture_output=True,check=True,timeout=60)
    report['private_custody_pushed']=True
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--master-key-id',required=True)
    p.add_argument('--apply',action='store_true');p.add_argument('--output',required=True,type=Path)
    args=p.parse_args()
    if args.output.exists():raise SystemExit('Preserve previous receipt')
    try:r=provision(args.master_key_id,args.apply);code=0
    except Exception as e:r={'completed':False,'error_type':type(e).__name__};code=1
    args.output.write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r));raise SystemExit(code)
