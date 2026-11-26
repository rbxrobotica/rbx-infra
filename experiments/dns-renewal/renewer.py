#!/usr/bin/env python3
"""Hourly, single-writer lab signing and constrained publication with fail-closed alerts."""
import argparse
import base64
import datetime
import fcntl
import hashlib
import json
import os
import pathlib
import resource
import subprocess
import time

import core
import probe
from clock_status import read_clock
from signal_client import heartbeat


def remote(release, credentials, command, payload=None):
    args=['/usr/bin/ssh','-F','/dev/null','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
          '-o','IdentitiesOnly=yes','-o','IdentityAgent=none','-o','ConnectTimeout=10',
          '-o','UserKnownHostsFile='+str(release/'known_hosts'),
          '-i',str(credentials/'publisher'),'rbx-dns-publisher@161.97.147.76',command]
    result=subprocess.run(args,input=payload,capture_output=True,check=True,timeout=45)
    if len(result.stdout)>1024*1024: raise ValueError('oversized_public_response')
    return json.loads(result.stdout)


def clock_check(state, now):
    clock=read_clock()
    if not clock['synchronized']: raise ValueError('clock_not_synchronized')
    last=state.get('last_success_epoch',0)
    if now<last-120: raise ValueError('clock_moved_backwards')
    return clock


def execute(release, state_dir, credentials):
    _,baseline=core.policy(release)
    state_path=state_dir/'current.json'
    state=json.loads(state_path.read_text()) if state_path.exists() else {}
    now=int(time.time());clock=clock_check(state,now)
    inspected=remote(release,credentials,'inspect')
    raw=base64.b64decode(inspected['zone_base64'],validate=True)
    if len(raw)>core.MAX_ZONE or hashlib.sha256(raw).hexdigest()!=inspected['sha256']:
        raise ValueError('origin_public_digest')
    current=core.parse(raw)
    # Gateway already authenticates the old public signature at a recorded valid
    # instant if expired. Do not call an expired old publication live healthy.
    checked=core.validate(current,baseline,now=min(now,inspected['valid_until_epoch']-1))
    floor=state.get('serial',core.serial(baseline))
    if checked['serial']!=floor and not core.newer(checked['serial'],floor):
        raise ValueError('origin_serial_rollback')
    pending=state_dir/'pending.zone'
    if pending.exists():
        candidate=pending.read_bytes();candidate_zone=core.parse(candidate)
        # A committed candidate is retried byte-for-byte, never re-signed on an
        # upload timeout. If now too old, keep it for inspection and fail closed.
        new=core.validate(candidate_zone,baseline,minimum_remaining=core.RENEW_BEFORE,
                          floor=checked['serial'])
        mode='resume_pending_publication'
    elif not state or checked['valid_until_epoch']-now<=core.RENEW_BEFORE:
        candidate,new=core.renew((credentials/'signer').read_bytes(),baseline,checked['serial'],now)
        core.atomic(pending,candidate)
        mode='initial_automatic_publication' if not state else 'renewal_due'
    else:
        candidate=None;new=checked;mode='verified_noop'
    if candidate is not None:
        selected=remote(release,credentials,'publish',candidate)
        if selected['sha256']!=hashlib.sha256(candidate).hexdigest() or not selected['origin_reload_verified']:
            raise ValueError('origin_publication_unconfirmed')
    deadline=time.monotonic()+180
    while True:
        health=probe.run(baseline,new['serial'],warning=core.RENEW_BEFORE,
                         expected_serial=new['serial'])
        if health['exit_code']==0: break
        if time.monotonic()>=deadline: raise ValueError('managed_publication_unconfirmed')
        time.sleep(2)
    current_receipt={**new,'last_success_epoch':int(time.time()),'mode':mode,
                     'managed_checks':health['checks'],'managed_exit_code':health['exit_code']}
    if candidate is not None:
        core.atomic(state_dir/'current.zone',candidate)
    else:
        core.atomic(state_dir/'current.zone',raw)
    core.write_json(state_path,current_receipt)
    pending.unlink(missing_ok=True)
    return {**current_receipt,'publication_fully_verified':True,'clock':clock}


def main():
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release',type=pathlib.Path,required=True)
    parser.add_argument('--state-dir',type=pathlib.Path,required=True)
    args=parser.parse_args()
    args.state_dir.mkdir(mode=0o700,parents=True,exist_ok=True)
    credentials=pathlib.Path(os.environ['CREDENTIALS_DIRECTORY'])
    report={'observed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    code=1
    try:
        with (args.state_dir/'renewal.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            report.update(execute(args.release,args.state_dir,credentials));code=0
    except Exception as error:
        report.update({'error_type':type(error).__name__,'renewal_completed':False})
    try:
        heartbeat((credentials/'healthchecks').read_text().strip(),code!=0,
                  'synthetic DNS renewal '+('verified' if code==0 else 'failed'))
        report['remote_signal_confirmed']=True
    except Exception as error:
        report.update({'remote_signal_confirmed':False,'signal_error_type':type(error).__name__});code=1
    report['exit_code']=code
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    core.write_json(args.state_dir/(stamp+'.json'),report)
    for old in sorted(args.state_dir.glob('20*.json'))[:-168]:old.unlink()
    print(json.dumps(report))
    return code

if __name__=='__main__': raise SystemExit(main())
