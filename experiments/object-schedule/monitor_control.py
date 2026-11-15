#!/usr/bin/env python3
"""Configure only the named memory checks; owner management key never deploys."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess

spec=importlib.util.spec_from_file_location('hc',Path(__file__).parent.parent/'dns-monitor/healthchecks-controller.py')
hc=importlib.util.module_from_spec(spec);spec.loader.exec_module(hc)
CHECKS={
    'capture':('RBX memory capture B2','rbx-memory-capture-b2',21600,3600,'rbx/healthchecks/memory-capture'),
    'witness':('RBX memory archive witness','rbx-memory-archive-witness',900,900,'rbx/healthchecks/memory-witness'),
    'drill':('RBX memory alert delivery drill','rbx-memory-alert-delivery-drill',60,60,'rbx/healthchecks/memory-drill')}


def reconcile(role,apply=False,transport=None,key=None,secret_store=None):
    if role not in CHECKS:raise ValueError('unknown_check_role')
    transport=transport or hc.api;key=key or hc.stored(hc.API_ENTRY)
    name,slug,period,grace,entry=CHECKS[role]
    before=transport(key)['checks']
    matches=[x for x in before if x['name']==name]
    if len(matches)>1:raise ValueError('duplicate_check_name')
    old=matches[0] if matches else None
    if old and (old.get('slug')!=slug or old.get('schedule')):
        raise ValueError('existing_check_not_owned')
    desired={'name':name,'slug':slug,'tags':'rbx memory backup '+role,
             'timeout':period,'grace':grace,'channels':'*','unique':['name']}
    changed=old is None or not old.get('channels') or any(
        old.get(k)!=v for k,v in desired.items() if k not in ('channels','unique'))
    report={'name':name,'role':role,'period_seconds':period,'grace_seconds':grace,
            'other_checks_preserved':len(before)-len(matches),'change_needed':changed,
            'apply_requested':apply,'paid_plan':False}
    if not apply:return report
    check=transport(key,hc.API if old is None else old['update_url'],desired) if changed else old
    if check['name']!=name or not re.fullmatch(r'https://hc-ping\.com/[0-9a-f-]{36}',check['ping_url']):
        raise ValueError('unexpected_check_response')
    if not check.get('channels'):raise ValueError('notification_channel_missing')
    if secret_store:
        secret_store(entry,check['ping_url'])
    else:
        path=Path.home()/'.password-store'/(entry+'.gpg')
        if path.exists():
            if hc.stored(entry)!=check['ping_url']:raise ValueError('ping_custody_differs')
        else:
            subprocess.run(['pass','insert','--multiline',entry],
                input=(check['ping_url']+'\n').encode(),capture_output=True,check=True,timeout=40)
    after=transport(key)['checks']
    protected={x['uuid']:x for x in before if x['name']!=name}
    for c in after:
        if c['uuid'] in protected:
            p=protected.pop(c['uuid'])
            if any(c.get(k)!=p.get(k) for k in ('name','slug','timeout','grace','channels','schedule')):
                raise ValueError('unrelated_check_changed')
    if protected:raise ValueError('unrelated_check_missing')
    report.update(reconciled=True,notification_channel_attached=True,ping_stored_encrypted=True)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('role',choices=CHECKS)
    parser.add_argument('--apply',action='store_true');parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists():raise SystemExit('Preserve previous receipt')
    try:r=reconcile(args.role,args.apply);code=0
    except Exception as e:r={'completed':False,'error_type':type(e).__name__};code=1
    args.output.write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r));raise SystemExit(code)
