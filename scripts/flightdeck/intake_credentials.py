#!/usr/bin/env python3
"""Narrow Ansible-backed addition of intake fields to the existing app Secret.

Never prints a Secret object, raw credential, connection string or upstream error.
The raw capture token never enters Kubernetes. Other Secret fields are preserved.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid


def command(argv, *, payload=None, maximum=1024*1024):
    result=subprocess.run(argv,input=payload,capture_output=True,timeout=30)
    if result.returncode or len(result.stdout)>maximum:raise RuntimeError('Controlled credential operation failed')
    return result.stdout


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kubeconfig',type=Path,required=True)
    parser.add_argument('--expected-cluster-uid',required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    if args.kubeconfig.stat().st_mode & 0o077:raise RuntimeError('Operational access file must be private')
    kubectl=['kubectl','--kubeconfig',str(args.kubeconfig)]
    def get(kind,name,namespace):
        return json.loads(command(kubectl+['get',kind,name,'-n',namespace,'-o','json']))
    cluster=get('namespace','kube-system','default')
    if cluster['metadata']['uid']!=args.expected_cluster_uid:raise RuntimeError('Cluster identity differs')
    app=get('application','rbx-flightdeck','argocd')
    if app.get('spec',{}).get('syncPolicy',{}).get('automated') is not None or app.get('operation'):
        raise RuntimeError('An idle controlled FlightDeck hold is required')
    source=get('secret','rbx-flightdeck-secrets','rbx-ia-br')
    required={'DATABASE_URL','FLIGHTDECK_OPERATOR_EMAIL','OIDC_ALLOWED_EMAILS','SESSION_SECRET'}
    if not required.issubset(source.get('data',{})):raise RuntimeError('Required existing app fields are missing')
    optional={'PUBLIC_PRESENCE_WORKER_KEY','PUBLIC_PRESENCE_SERVICE_KEY','MAESTRO_ADMIT_KEY'}
    if optional.intersection(source.get('data',{})):
        raise RuntimeError('Optional integration changed since preflight; coordinate its existing activation')
    keys=['INTAKE_CHAT_TOKEN_SHA256','INTAKE_REVIEW_WORKER_KEY','INTAKE_CHAT_THREAD_ID']
    if not args.apply:
        print(json.dumps({'mode':'plan','namespace':'rbx-ia-br','secret':'rbx-flightdeck-secrets',
                          'add_or_verify_only':keys,'other_fields_preserved':True}));return
    tokens=[]
    for entry in ('rbx/flightdeck/chat-capture-token','rbx/flightdeck/review-worker-token'):
        value=command(['pass','show',entry],maximum=4096).decode().strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}',value):raise RuntimeError('Purpose credential has an invalid format')
        tokens.append(value)
    if tokens[0]==tokens[1]:raise RuntimeError('Capture and review credentials must differ')
    thread=command(['pass','show','rbx/flightdeck/chat-source-thread-id'],maximum=4096).decode().strip()
    if str(uuid.UUID(thread))!=thread:raise RuntimeError('Private source reference is invalid')
    values={keys[0]:hashlib.sha256(tokens[0].encode()).hexdigest(),keys[1]:tokens[1],keys[2]:thread}
    patch_data={key:base64.b64encode(value.encode()).decode() for key,value in values.items()}
    for key,value in patch_data.items():
        if key in source['data'] and source['data'][key]!=value:
            raise RuntimeError('Existing intake credential differs; coordinated rotation is required')
    if all(source['data'].get(key)==value for key,value in patch_data.items()):
        print(json.dumps({'state':'ALREADY_MATERIALIZED','keys':keys}));return
    patch={'metadata':{'resourceVersion':source['metadata']['resourceVersion']},'data':patch_data}
    command(kubectl+['patch','secret','rbx-flightdeck-secrets','-n','rbx-ia-br','--type=merge',
                    '--patch-file=/dev/stdin','-o','name'],payload=json.dumps(patch).encode())
    after=get('secret','rbx-flightdeck-secrets','rbx-ia-br')
    if any(after.get('data',{}).get(key)!=value for key,value in patch_data.items()):
        raise RuntimeError('Intake credential materialization was not verified')
    if any(after['data'].get(key)!=value for key,value in source['data'].items()):
        raise RuntimeError('An unrelated app field changed concurrently; coordinate before continuing')
    print(json.dumps({'state':'MATERIALIZED','namespace':'rbx-ia-br','secret':'rbx-flightdeck-secrets',
                      'keys':keys,'other_fields_preserved':True,'raw_capture_token_in_cluster':False}))


if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,ValueError,KeyError,subprocess.SubprocessError):
        print('Scoped intake credential operation failed; upstream details suppressed',file=sys.stderr);sys.exit(1)
