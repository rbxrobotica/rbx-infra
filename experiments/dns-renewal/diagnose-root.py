#!/usr/bin/env python3
"""Read-only, bounded replica of renewal sandbox; never signs, publishes or pings."""
import os
import pathlib
import subprocess
import tempfile

PROGRAM = '''import sys,pathlib,os,subprocess,json,stat
sys.path.insert(0,'/opt/rbx-dns-renewal')
import core,renewer
from cryptography.hazmat.primitives import serialization
release=pathlib.Path('/opt/rbx-dns-renewal')
credentials=pathlib.Path(os.environ['CREDENTIALS_DIRECTORY'])
report={"scope":"read-only sandbox diagnostic","signing_or_publication_attempted":False}
r=subprocess.run(['/usr/bin/timedatectl','show','-p','NTPSynchronized','--value'],capture_output=True,text=True)
report['clock_query']={'exit_code':r.returncode,'synchronized':r.stdout.strip()=='yes','access_denied':'denied' in r.stderr.lower(),'bus_unavailable':'bus' in r.stderr.lower()}
try:
 from clock_status import read_clock
 report['kernel_clock']=read_clock()
except Exception as e:report['kernel_clock']={'completed':False,'error_type':type(e).__name__}
try:
 key=serialization.load_ssh_private_key((credentials/'publisher').read_bytes(),password=None)
 report['publisher_private_format_valid']=True
except Exception as e:report['publisher_private_format_valid']=False;report['publisher_format_error_type']=type(e).__name__
report['publisher_credential_mode']=oct((credentials/'publisher').stat().st_mode & 0o777)
try:
 r=renewer.remote(release,credentials,'inspect')
 report['ssh']={'completed':True,'selected_serial':r['serial']}
except subprocess.CalledProcessError as e:
 text=(e.stderr or b'').decode(errors='replace').lower()
 patterns={'uid_unknown':'no user exists for uid','key_format':'error in libcrypto','key_permissions':'bad permissions','key_unprotected':'unprotected private key','identity_unreadable':'identity file','publickey_rejected':'permission denied','host_key':'host key verification failed','unknown_ed25519_host':'no ed25519 host key','connection_timeout':'timed out','connection_refused':'connection refused','connection_closed':'connection closed','key_load':'load key','not_found':'no such file or directory'}
 report['ssh']={'completed':False,'exit_code':e.returncode,'categories':[k for k,v in patterns.items() if v in text],'stderr_sha256':__import__('hashlib').sha256(e.stderr or b'').hexdigest()}
except Exception as e:report['ssh']={'completed':False,'error_type':type(e).__name__}
print(json.dumps(report))
'''

def main():
    if os.geteuid()!=0:raise SystemExit('Administrative authentication is required')
    source=pathlib.Path('/etc/systemd/system/rbx-dns-pilot-renewal.service').read_text()
    allowed=['DynamicUser','User','NoNewPrivileges','ProtectSystem','ProtectHome','PrivateTmp','PrivateDevices',
             'ProtectKernelTunables','ProtectKernelModules','ProtectControlGroups','RestrictAddressFamilies',
             'RestrictSUIDSGID','LockPersonality','CapabilityBoundingSet','MemoryMax','CPUQuota','TasksMax','LimitCORE']
    props=[line for line in source.splitlines() if '=' in line and line.split('=',1)[0] in allowed]
    props+=['LoadCredentialEncrypted=publisher:/etc/credstore.encrypted/rbx-dns-renewal-publisher','RuntimeMaxSec=60']
    with tempfile.TemporaryDirectory(prefix='rbx-dns-renew-diagnostic-',dir='/run') as work:
        path=pathlib.Path(work);path.chmod(0o755)
        program=path/'diagnostic.py';program.write_text(PROGRAM);program.chmod(0o444)
        command=['systemd-run','--unit=rbx-dns-renew-diagnostic','--wait','--pipe','--collect']
        for prop in props:command+=['--property',prop]
        command+=['/usr/bin/python3','-B',str(program)]
        raise SystemExit(subprocess.run(command,timeout=90).returncode)

if __name__=='__main__':main()
