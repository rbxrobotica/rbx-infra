#!/usr/bin/env python3
"""One-Secret recovery behind live cluster, ArgoCD and migration hold checks.

Called by the dedicated Ansible playbook. Default is read-only. Decrypted
credentials never become Ansible facts, module arguments, files or log output.
"""
import argparse
import base64
import json
from pathlib import Path
import subprocess


class RecoveryError(Exception):
    """Only fixed diagnostic codes are exposed."""


def command(argv, *, label, data=None):
    try:
        p = subprocess.run(argv,input=data,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                           timeout=30,check=False)
    except (OSError,subprocess.SubprocessError):
        raise RecoveryError(label+'_failed') from None
    if p.returncode or len(p.stdout)>256*1024:
        raise RecoveryError(label+'_failed')
    return p.stdout


def recover(kubeconfig, expected_uid, *, apply=False):
    kubeconfig=str(Path(kubeconfig).expanduser())
    kube=['kubectl','--kubeconfig',kubeconfig,'--request-timeout=10s']
    uid=command(kube+['get','namespace','kube-system','-o','jsonpath={.metadata.uid}'],label='cluster_identity')
    if uid.decode().strip()!=expected_uid:
        raise RecoveryError('different_production_cluster')

    def read(args, label, empty=False):
        raw=command(kube+args,label=label)
        if empty and not raw.strip():
            return None
        try:
            return json.loads(raw or (b'{}' if empty else b''))
        except (ValueError,UnicodeError):
            raise RecoveryError(label+'_invalid_metadata') from None

    app=read(['-n','argocd','get','application','rbx-public-presence','-o','json'],'application')
    if (app.get('kind')!='Application'
            or app.get('metadata',{}).get('name')!='rbx-public-presence'
            or app.get('metadata',{}).get('namespace')!='argocd'
            or app.get('spec',{}).get('destination',{}).get('namespace')!='rbx-public-presence'):
        raise RecoveryError('unexpected_application_metadata')
    if (app.get('spec',{}).get('syncPolicy',{}).get('automated') is not None
            or app.get('operation') is not None
            or app.get('status',{}).get('operationState',{}).get('phase')=='Running'):
        raise RecoveryError('application_not_held')
    job=read(['-n','rbx-public-presence','get','job','rbx-public-presence-db-migrate',
              '--ignore-not-found','-o','json'],'migration',empty=True)
    if job is not None and (job.get('kind')!='Job'
            or job.get('metadata',{}).get('name')!='rbx-public-presence-db-migrate'
            or job.get('metadata',{}).get('namespace')!='rbx-public-presence'
            or job.get('spec',{}).get('suspend') is not True
            or job.get('status',{}).get('active',0)!=0
            or job.get('status',{}).get('terminating',0)!=0):
        raise RecoveryError('migration_not_fully_suspended')
    pods=read(['-n','rbx-public-presence','get','pods','-l',
               'job-name=rbx-public-presence-db-migrate','-o','json'],'migration_pods')
    if 'items' not in pods or any(p.get('status',{}).get('phase') not in ('Succeeded','Failed')
                                for p in pods['items']):
        raise RecoveryError('nonterminal_migration_pod')
    if apply:
        raw=command(['pass','show','rbx/cluster/ghcr-token'],label='credential_read')
        if not raw.strip() or len(raw)>8192:
            raise RecoveryError('invalid_registry_credential')
        token=raw.splitlines()[0].strip()
        auth=base64.b64encode(b'rbxrobotica:'+token).decode()
        config=json.dumps({'auths':{'ghcr.io':{'auth':auth}}}).encode()
        payload={'apiVersion':'v1','kind':'Secret',
                 'metadata':{'name':'ghcr-pull-secret','namespace':'rbx-public-presence'},
                 'type':'kubernetes.io/dockerconfigjson',
                 'data':{'.dockerconfigjson':base64.b64encode(config).decode()}}
        command(kube+['apply','--server-side','--field-manager=rbx-presence-recovery','-f','-'],
                label='namespaced_secret_apply',data=json.dumps(payload).encode())
    return {'schema':'rbx.public-presence.registry-recovery.v1','application_held':True,
            'migration_held':True,'secret_namespace':'rbx-public-presence',
            'secret_name':'ghcr-pull-secret','applied':apply}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kubeconfig',required=True)
    parser.add_argument('--expected-cluster-uid',required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    try:
        print(json.dumps(recover(args.kubeconfig,args.expected_cluster_uid,apply=args.apply)))
    except RecoveryError as exc:
        print(json.dumps({'error':str(exc),'applied':False}))
        return 1
    except (ValueError,TypeError,KeyError,AttributeError):
        print(json.dumps({'error':'invalid_recovery_metadata','applied':False}))
        return 1
    return 0


if __name__=='__main__':
    raise SystemExit(main())
