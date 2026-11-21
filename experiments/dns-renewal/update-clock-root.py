#!/usr/bin/env python3
"""Reviewed two-file repair; preserve credentials, units, policy and publication state."""
import hashlib
import json
import os
import pathlib
import subprocess

OLD_SHA='04330535f9d005c46c3f3eb20292f3d063a7de1f37b62ff909ef16a99b935fc6'

def main():
    if os.geteuid()!=0:raise ValueError('administrative_authentication_required')
    source=pathlib.Path(__file__).resolve().parent
    installed=pathlib.Path('/opt/rbx-dns-renewal/renewer.py')
    digest=hashlib.sha256(installed.read_bytes()).hexdigest()
    desired=hashlib.sha256((source/'renewer.py').read_bytes()).hexdigest()
    if installed.is_symlink() or installed.stat().st_uid!=0 or digest not in (OLD_SHA,desired):
        raise ValueError('unreviewed_renewer_drift')
    for name in ['core.py','probe.py','monitor.py','signal_client.py']:
        path=installed.parent/name
        if path.is_symlink() or path.stat().st_uid!=0 or path.read_bytes()!=(source/name).read_bytes():
            raise ValueError('unreviewed_public_release_drift')
    state=subprocess.check_output(['systemctl','show','rbx-dns-pilot-renewal.service','-p','ActiveState','--value'],text=True).strip()
    if state in ('active','activating','deactivating'):raise ValueError('renewal_in_progress')
    command=['ansible-playbook','-i','localhost,',str(source/'update-clock.yml'),'-e',json.dumps({'renewal_source':str(source),'ansible_python_interpreter':'/usr/bin/python3'})]
    for flags in [['--check','--diff'],[],['--check']]:subprocess.run(command+flags,check=True,timeout=60)
    diagnostic=subprocess.run(['/usr/bin/python3','-B',str(source/'diagnose-root.py')],capture_output=True,text=True,check=True,timeout=100)
    reports=[json.loads(line) for line in diagnostic.stdout.splitlines() if line.startswith('{')]
    if len(reports)!=1 or not reports[0].get('kernel_clock',{}).get('synchronized') or not reports[0].get('ssh',{}).get('completed'):
        raise ValueError('repaired_sandbox_not_verified')
    print(json.dumps({'read_only_sandbox_diagnostic':reports[0]}),flush=True)
    for unit in ['rbx-dns-pilot-renewal.service','rbx-dns-pilot-renewal.service','rbx-dns-pilot-monitor.service']:
        subprocess.run(['systemctl','start',unit],check=True,timeout=310)
    print(json.dumps({'reviewed_clock_repair_applied':True,'credentials_or_trust_pins_modified':False,
                      'renewal_and_subsequent_noop_requested':True,'independent_monitor_requested':True}))

if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'repair_completed':False,'error_type':type(error).__name__}));raise SystemExit(1)
