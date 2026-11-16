#!/usr/bin/env python3
"""Install an idempotent daily encrypted owner-copy timer from reviewed source."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();os.umask(0o077)
    scripts=Path(__file__).resolve().parent.parent
    root=Path.home()/'.local/share/rbx-flightdeck-tools/backup'
    unit=Path.home()/'.config/systemd/user/rbx-flightdeck-owner-copy.service'
    timer=unit.with_suffix('.timer')
    files=[(root/'owner_copy.py',(scripts/'flightdeck/owner_copy.py').read_bytes()),
           (root/'restore_check.py',(scripts/'flightdeck/restore_check.py').read_bytes()),
           (root/'plausible/s3_archive.py',(scripts/'plausible/s3_archive.py').read_bytes()),
           (root/'plausible/backup_runner.py',(scripts/'plausible/backup_runner.py').read_bytes())]
    service='''[Unit]
Description=Verify and retain an encrypted FlightDeck archive outside the source cluster
StartLimitIntervalSec=1h
StartLimitBurst=3

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 %h/.local/share/rbx-flightdeck-tools/backup/owner_copy.py
TimeoutStartSec=360
Restart=on-failure
RestartSec=30s
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
MemoryMax=256M
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
'''
    schedule='''[Unit]
Description=Daily independent encrypted copy of the FlightDeck PostgreSQL backup

[Timer]
OnCalendar=*-*-* 05:30:00 America/Sao_Paulo
Persistent=true
RandomizedDelaySec=60
Unit=rbx-flightdeck-owner-copy.service

[Install]
WantedBy=timers.target
'''
    files.extend([(unit,service.encode()),(timer,schedule.encode())])
    revision=subprocess.run(['git','rev-parse','HEAD'],cwd=scripts.parent,capture_output=True,check=True,timeout=10).stdout.decode().strip()
    source={'repository':'rbxrobotica/rbx-infra','commit':revision,'files_sha256':
        {str(path.relative_to(root)):hashlib.sha256(data).hexdigest() for path,data in files if path.is_relative_to(root)}}
    if not args.apply:
        print(json.dumps({'mode':'plan','targets':[str(path) for path,_ in files],'source':source}));return
    for path,data in files+[(root/'source.json',(json.dumps(source,indent=2)+'\n').encode())]:
        path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        temporary=path.with_suffix(path.suffix+'.new')
        with temporary.open('xb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    subprocess.run(['systemctl','--user','daemon-reload'],check=True,capture_output=True)
    subprocess.run(['systemctl','--user','enable','--now','rbx-flightdeck-owner-copy.timer'],check=True,capture_output=True)
    # Re-arm an already-active timer against the new calendar. Persistent=true
    # catches a missed eligible copy without explicitly starting the service.
    subprocess.run(['systemctl','--user','restart','rbx-flightdeck-owner-copy.timer'],check=True,capture_output=True)
    print(json.dumps({'state':'INSTALLED','schedule':'05:30 America/Sao_Paulo','source':source}))


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,subprocess.SubprocessError):
        print('Owner copy installation failed; upstream output suppressed',file=sys.stderr);sys.exit(1)
