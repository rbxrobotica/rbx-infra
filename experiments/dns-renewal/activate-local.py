#!/usr/bin/env python3
"""Operator-terminal activation; private service material goes only through stdin."""
import argparse
import base64
import json
import pathlib
import subprocess

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--public-stage',required=True,type=pathlib.Path)
    p.add_argument('--resilience',required=True,type=pathlib.Path)
    args=p.parse_args()
    subprocess.run(['sudo','-v'],check=True)
    material={}
    for name,entry in [('signer','rbx/dns/pilot-signer'),('publisher','rbx/dns/publisher-ssh'),
                       ('healthchecks','rbx/healthchecks/dns-renewal')]:
        data=subprocess.run(['pass','show',entry],capture_output=True,check=True,timeout=45).stdout
        material[name]=base64.b64encode(data).decode()
    command=['sudo','-n','/usr/bin/python3','-B',str(pathlib.Path(__file__).resolve().with_name('activate-root.py')),
             '--public-stage',str(args.public_stage.resolve()),'--resilience',str(args.resilience.resolve())]
    result=subprocess.run(command,input=json.dumps(material).encode(),timeout=420)
    raise SystemExit(result.returncode)

if __name__=='__main__':main()
