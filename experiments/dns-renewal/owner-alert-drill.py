#!/usr/bin/env python3
"""Finite owner-only origin outage/recovery drill; never part of runtime service."""
import argparse
import json
import os
import pathlib
import re
import resource
import signal
import stat
import subprocess
import time
from unittest import mock

import core
import renewer
from signal_client import heartbeat


def main():
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admin-agent',required=True,type=pathlib.Path)
    parser.add_argument('--release',required=True,type=pathlib.Path)
    parser.add_argument('--public-key',required=True,type=pathlib.Path)
    parser.add_argument('--work-dir',required=True,type=pathlib.Path)
    parser.add_argument('--hold-down-seconds',type=int,default=20)
    args=parser.parse_args()
    if not 1<=args.hold_down_seconds<=45:raise ValueError('bounded_drill_only')
    meta=args.admin_agent.stat()
    if not stat.S_ISSOCK(meta.st_mode) or meta.st_uid!=os.getuid():raise ValueError('unexpected_owner_agent')
    args.work_dir.mkdir(mode=0o700,parents=True,exist_ok=False)
    # Do not overlap a naturally scheduled renewal: this is a manual owner drill,
    # not a claim that the protected systemd unit failed or recovered by itself.
    next_run=subprocess.run(['systemctl','show','rbx-dns-pilot-renewal.timer','--property=NextElapseUSecRealtime','--value'],capture_output=True,text=True,check=True).stdout.strip()
    if not next_run:raise ValueError('installed_timer_required')
    deadline=subprocess.run(['date','-d',next_run,'+%s'],capture_output=True,text=True,check=True).stdout.strip()
    if int(deadline)-time.time()<180:raise ValueError('too_close_to_scheduled_renewal')
    def stored(entry):return subprocess.run(['pass','show',entry],capture_output=True,check=True,timeout=30).stdout
    signal_url=stored('rbx/healthchecks/dns-renewal').decode().strip()
    publisher=stored('rbx/dns/publisher-ssh')
    sock=args.work_dir/'publisher-agent.sock'
    output=subprocess.run(['ssh-agent','-a',str(sock)],capture_output=True,text=True,check=True).stdout
    pid=int(re.search(r'SSH_AGENT_PID=(\d+)',output).group(1))
    admin_base=['ssh','-F','/dev/null','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10','-o','IdentityAgent='+str(args.admin_agent),'root@161.97.147.76']
    def admin(action):
        assert action in ('start','stop')
        subprocess.run(admin_base+['systemctl '+action+' rbx-dns-renew-origin.service'],capture_output=True,check=True,timeout=30)
    result={'scope':'owner-run real origin outage; same renewal orchestration with ephemeral constrained SSH agent transport',
            'protected_systemd_unit_failure_claimed':False,'scheduled_renewal_not_disabled':True,
            'original_zone_or_key_modified':False}
    stopped=False
    try:
        subprocess.run(['ssh-add','-t','180','-'],input=publisher,capture_output=True,check=True,timeout=10,
                       env={**os.environ,'SSH_AUTH_SOCK':str(sock)})
        def remote(release,credentials,command,payload=None):
            if command!='inspect' or payload is not None:raise ValueError('drill_must_not_publish')
            ssh=['ssh','-F','/dev/null','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=5',
                 '-o','IdentitiesOnly=yes','-o','IdentityAgent='+str(sock),'-i',str(args.public_key),
                 'rbx-dns-publisher@161.97.147.76',command]
            response=subprocess.run(ssh,capture_output=True,check=True,timeout=15)
            return json.loads(response.stdout)
        current=remote(args.release,None,'inspect')
        if current['valid_until_epoch']-time.time()<=core.RENEW_BEFORE+180:
            raise ValueError('cannot_drill_close_to_expiration')
        state=args.work_dir/'public-state';state.mkdir()
        core.write_json(state/'current.json',{'serial':current['serial'],'last_success_epoch':int(time.time())})
        began=time.monotonic();admin('stop');stopped=True
        with mock.patch.object(renewer,'remote',side_effect=remote):
            try:renewer.execute(args.release,state,None)
            except Exception as error:
                result['real_pipeline_failure_type']=type(error).__name__
            else:raise ValueError('outage_was_not_detected')
            heartbeat(signal_url,True,'OWNER DRILL: actual selected origin unavailable')
            result['failure_signal_http_confirmed']=True
            time.sleep(args.hold_down_seconds)
            admin('start');stopped=False
            result['recovery']=renewer.execute(args.release,state,None)
            if result['recovery']['mode']!='verified_noop':raise ValueError('unexpected_drill_publication')
            heartbeat(signal_url,False,'OWNER DRILL: origin restored and managed DNS verified')
            result['recovery_signal_http_confirmed']=True
        result['elapsed_seconds']=round(time.monotonic()-began,3)
        result['result']='pass'
    finally:
        if stopped:admin('start')
        os.kill(pid,signal.SIGTERM)
        sock.unlink(missing_ok=True)
        core.write_json(args.work_dir/'receipt.json',result)
    print(json.dumps(result))

if __name__=='__main__':main()
