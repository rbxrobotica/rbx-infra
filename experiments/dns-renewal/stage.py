#!/usr/bin/env python3
"""Build a bounded public-only release from reviewed source and known NSD tools."""
import argparse
import hashlib
import json
import pathlib
import shutil
import subprocess

NSD_SHA='44e1d576f03084965763c4979942ba158dc8e38b00adedd67ed3739d02da44a2'
TOOLS=['usr/sbin/nsd','usr/sbin/nsd-checkconf','usr/sbin/nsd-checkzone',
       'usr/lib/x86_64-linux-gnu/libevent-2.1.so.7','usr/lib/x86_64-linux-gnu/libfstrm.so.0',
       'usr/lib/x86_64-linux-gnu/libhiredis.so.1','usr/lib/x86_64-linux-gnu/libhiredis_ssl.so.1',
       'usr/lib/x86_64-linux-gnu/libprotobuf-c.so.1']

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ['resilience','tools-root','out']:p.add_argument('--'+n,type=pathlib.Path,required=True)
    args=p.parse_args();args.out.mkdir(mode=0o700,parents=True,exist_ok=False)
    source=pathlib.Path(__file__).parent
    fixture=args.resilience/'fixtures/dns/signer-recovery-20261113/recovered'
    raw=(fixture/'reference.zone').read_bytes();publication=json.loads((fixture/'publication.json').read_text())
    if hashlib.sha256(raw).hexdigest()!=publication['sha256']:raise ValueError('baseline_digest')
    policy={'origin':'cloudns-xfr-pilot.rbxsystems.ch.','synthetic_only':True,
            'baseline_sha256':publication['sha256'],'ds_sha256':publication['ds_sha256'],
            'signature_lifetime_seconds':604800,'renew_before_seconds':259200,
            'renewal_period_seconds':3600,'renewal_grace_seconds':3600}
    shared=['core.py','probe.py','signal_client.py']
    for name,files in [('local',shared+['renewer.py','monitor.py']),
                       ('origin',shared+['origin-gateway.py','firewall.py'])]:
        dest=args.out/name;dest.mkdir(mode=0o755)
        for file in files:shutil.copyfile(source/file,dest/file)
        (dest/'baseline.zone').write_bytes(raw)
        (dest/'policy.json').write_text(json.dumps(policy,indent=2)+'\n')
    (args.out/'baseline.zone').write_bytes(raw)
    known=subprocess.run(['ssh-keygen','-F','161.97.147.76'],capture_output=True,text=True,check=True).stdout
    known='\n'.join(l for l in known.splitlines() if not l.startswith('#'))+'\n'
    if not known.strip():raise ValueError('known_host_missing')
    (args.out/'local/known_hosts').write_text(known)
    if hashlib.sha256((args.tools_root/'usr/sbin/nsd').read_bytes()).hexdigest()!=NSD_SHA:raise ValueError('nsd_digest')
    for file in TOOLS:
        dest=args.out/'origin/tools'/file;dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(args.tools_root/file,dest)
    vendor=pathlib.Path('/usr/lib/python3/dist-packages/dns')
    shutil.copytree(vendor,args.out/'origin/vendor/dns',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    license_path=pathlib.Path('/usr/share/doc/python3-dnspython/copyright')
    shutil.copyfile(license_path,args.out/'origin/vendor/dnspython-copyright')
    conf=(source.parent/'dns-origin/nsd-origin.conf.in').read_text()
    conf=conf.replace('/var/cache/rbx-dns-origin-source','/var/lib/rbx-dns-renew-origin').replace('/run/rbx-dns-origin-state','/run/rbx-dns-renew-origin')
    partners=['109.201.133.251','178.156.179.118','51.91.57.244','185.206.180.55','45.83.248.12','45.83.249.12','45.83.250.12','45.83.251.12','185.206.180.204']
    acl='\n'.join('    provide-xfr: '+ip+' NOKEY\n    notify: '+ip+' NOKEY' for ip in partners)
    conf=conf.replace('<provider-zone-transfer-acl>',acl+'\n    provide-xfr: 161.97.147.76 NOKEY')
    (args.out/'origin/nsd.conf').write_text(conf)
    inventory={}
    for path in args.out.rglob('*'):
        if path.is_file():
            path.chmod(0o555 if '/usr/sbin/' in str(path) else 0o444)
            inventory[str(path.relative_to(args.out))]=hashlib.sha256(path.read_bytes()).hexdigest()
        elif path.is_dir():path.chmod(0o755)
    (args.out/'manifest.json').write_text(json.dumps(inventory,indent=2)+'\n')
    print(json.dumps({'public_only_files':len(inventory),'nsd_sha256':NSD_SHA,'stage':str(args.out)}))

if __name__=='__main__':main()
