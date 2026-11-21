#!/usr/bin/env python3
"""Scoped initial renewal activation and reviewed monitor override, with drift guard."""
import argparse
import base64
import hashlib
import json
import os
import pathlib
import re
import resource
import subprocess
import sys
import tempfile

def main():
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--public-stage',type=pathlib.Path,required=True)
    p.add_argument('--resilience',type=pathlib.Path,required=True)
    args=p.parse_args()
    if os.geteuid()!=0:raise ValueError('root_authentication_required')
    source=pathlib.Path(__file__).resolve().parent
    old=source.parent/'dns-monitor'
    fixture=args.resilience/'fixtures/dns/signer-recovery-20261113/recovered'
    pairs=[('/opt/rbx-dns-monitor/worker.py',old/'worker.py'),
           ('/opt/rbx-dns-monitor/checker.py',args.resilience/'scripts/check-transfer-pilot-health.py'),
           ('/opt/rbx-dns-monitor/reference.zone',fixture/'reference.zone'),
           ('/opt/rbx-dns-monitor/publication.json',fixture/'publication.json'),
           ('/etc/systemd/system/rbx-dns-pilot-monitor.service',old/'monitor.service'),
           ('/etc/systemd/system/rbx-dns-pilot-monitor.timer',old/'monitor.timer')]
    for installed,reviewed in pairs:
        a=pathlib.Path(installed)
        if a.is_symlink() or a.stat().st_uid!=0 or a.read_bytes()!=reviewed.read_bytes():
            raise ValueError('existing_monitor_drift')
    for name in ['/opt/rbx-dns-renewal','/etc/systemd/system/rbx-dns-pilot-monitor.service.d',
                 '/etc/systemd/system/rbx-dns-pilot-renewal.service',
                 '/etc/systemd/system/rbx-dns-pilot-renewal.timer']:
        if pathlib.Path(name).exists():raise ValueError('renewal_state_already_exists_requires_review')
    manifest=json.loads((args.public_stage/'manifest.json').read_text())
    for relative,digest in manifest.items():
        path=args.public_stage/relative
        if not path.resolve().is_relative_to(args.public_stage.resolve()) or path.is_symlink():
            raise ValueError('unsafe_public_stage')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('public_stage_drift')
    sys.path.insert(0,str(source))
    import core
    from cryptography.hazmat.primitives import serialization
    import dns.dnssec
    config,baseline=core.policy(args.public_stage/'local')
    payload=json.loads(sys.stdin.buffer.read(32769))
    if set(payload)!={'signer','publisher','healthchecks'}:raise ValueError('credential_set')
    values={k:base64.b64decode(v,validate=True) for k,v in payload.items()}
    signer=serialization.load_pem_private_key(values['signer'],password=None)
    if dns.dnssec.make_ds(core.ORIGIN,dns.dnssec.make_dnskey(signer.public_key(),15,flags=257),'SHA256').to_text()!=config['ds_sha256']:
        raise ValueError('runtime_signer_does_not_match_anchor')
    publisher=serialization.load_ssh_private_key(values['publisher'],password=None)
    public=publisher.public_key().public_bytes(serialization.Encoding.OpenSSH,serialization.PublicFormat.OpenSSH)
    if not (args.public_stage/'publisher-public.key').read_bytes().startswith(public+b' '):raise ValueError('publisher_key_mismatch')
    if not re.fullmatch(rb'https://hc-ping\.com/[0-9a-f-]{36}',values['healthchecks'].strip()):raise ValueError('signal_destination')
    with tempfile.TemporaryDirectory(prefix='rbx-dns-renewal-wrapped-') as work:
        work=pathlib.Path(work)
        for name,value in values.items():
            wrapped=subprocess.run(['/usr/bin/systemd-creds','encrypt','--with-key=host','--name='+name,'-','-'],
                                   input=value.strip()+b'\n' if name!='healthchecks' else value.strip(),
                                   capture_output=True,check=True,timeout=20).stdout
            (work/(name+'.encrypted')).write_bytes(wrapped)
        variables={'public_stage':str(args.public_stage.resolve()),'wrapped_credentials':str(work),
                   'ansible_python_interpreter':'/usr/bin/python3'}
        varfile=work/'public-vars.json';varfile.write_text(json.dumps(variables))
        command=['/usr/bin/ansible-playbook','-i','localhost,',str(source/'install-local.yml'),'-e','@'+str(varfile)]
        subprocess.run(command+['--check','--diff'],check=True,timeout=90)
        subprocess.run(command,check=True,timeout=90)
        subprocess.run(command+['--check'],check=True,timeout=90)
    # Runs may also have started from the persistent/boot timer. systemd serializes
    # the exact oneshot; no second independent signing process is launched here.
    subprocess.run(['systemctl','start','rbx-dns-pilot-renewal.service'],check=True,timeout=310)
    subprocess.run(['systemctl','start','rbx-dns-pilot-monitor.service'],check=True,timeout=90)
    print(json.dumps({'protected_renewal_installed':True,'reviewed_monitor_override_installed':True,
                      'plaintext_on_persistent_storage':False,'owner_gpg_or_admin_ssh_deployed':False}))

if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'activation_completed':False,'error_type':type(error).__name__}));raise SystemExit(1)
