#!/usr/bin/env python3
"""Independent fixed-key/content monitor with persistent serial rollback detection."""
import argparse
import datetime
import json
import os
import pathlib
import time
import core
import probe
from signal_client import heartbeat


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release',required=True,type=pathlib.Path)
    parser.add_argument('--state-dir',required=True,type=pathlib.Path)
    args=parser.parse_args();args.state_dir.mkdir(mode=0o700,parents=True,exist_ok=True)
    report={'observed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()};code=3
    try:
        _,baseline=core.policy(args.release)
        highwater=args.state_dir/'serial-floor.json'
        floor=json.loads(highwater.read_text())['serial'] if highwater.exists() else core.serial(baseline)
        # Local rollback floor is a reconstructible observation cache. The
        # immutable baseline and independent DS/content pin remain the trust root.
        if floor!=core.serial(baseline) and not core.newer(floor,core.serial(baseline)):
            raise ValueError('invalid_serial_floor')
        deadline=time.monotonic()+45
        while True:
            report.update(probe.run(baseline,floor));code=report['exit_code']
            if code==0 or time.monotonic()>=deadline:break
            time.sleep(2)
        if code==0:core.write_json(highwater,{'serial':report['serials'][0]})
    except Exception as error:
        report.update({'error_type':type(error).__name__});code=3
    try:
        credentials=pathlib.Path(os.environ['CREDENTIALS_DIRECTORY'])
        heartbeat((credentials/'healthchecks').read_text().strip(),code!=0,
                  'synthetic renewable DNS exit='+str(code))
        report['remote_signal_confirmed']=True
    except Exception as error:
        report.update({'remote_signal_confirmed':False,'signal_error_type':type(error).__name__});code=3
    report['exit_code']=code
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    core.write_json(args.state_dir/(stamp+'.json'),report)
    for old in sorted(args.state_dir.glob('20*.json'))[:-672]:old.unlink()
    print(json.dumps({'dns_exit_code':code,'remote_signal_confirmed':report['remote_signal_confirmed'],
                      'serials':report.get('serials',[])}))
    return code

if __name__=='__main__':raise SystemExit(main())
