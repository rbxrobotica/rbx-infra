#!/usr/bin/env python3
"""Owner-only preparation, private-remote fetch and narrow credential restore."""
import argparse
import base64
import hashlib
import importlib.util
import json
import pathlib
import subprocess
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

parser=argparse.ArgumentParser(description='Prepare only dedicated lab publisher/signal custody; keep all private values in memory or encrypted pass.')
parser.add_argument('--work-dir',required=True,type=pathlib.Path)
args=parser.parse_args()
CACHE=args.work_dir
CACHE.mkdir(mode=0o700,parents=True,exist_ok=True)
STORE=pathlib.Path.home()/'.password-store'
ENTRY='rbx/dns/publisher-ssh'
try:
    encrypted=STORE/(ENTRY+'.gpg')
    if encrypted.exists():
        raw=subprocess.run(['pass','show',ENTRY],capture_output=True,check=True,timeout=30).stdout
        key=serialization.load_ssh_private_key(raw,password=None)
        created=False
    else:
        key=ed25519.Ed25519PrivateKey.generate()
        raw=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.OpenSSH,serialization.NoEncryption())
        subprocess.run(['pass','insert','--multiline',ENTRY],input=raw,capture_output=True,check=True,timeout=45)
        created=True
    public=key.public_key().public_bytes(serialization.Encoding.OpenSSH,serialization.PublicFormat.OpenSSH)+b' rbx-dns-pilot-publisher\n'
    (CACHE/'publisher-public.key').write_bytes(public)
    spec=importlib.util.spec_from_file_location('hc',pathlib.Path(__file__).resolve().parent.parent/'dns-monitor/healthchecks-controller.py')
    h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
    api_key=h.stored(h.API_ENTRY);before=h.api(api_key)['checks']
    matches=[x for x in before if x['name']=='RBX DNS renewal']
    if len(matches)>1:raise ValueError('duplicate_renewal_check')
    desired={'name':'RBX DNS renewal','slug':'rbx-dns-renewal','tags':'rbx synthetic dns renewal','timeout':3600,'grace':3600,'channels':'*','unique':['name']}
    if matches:
        c=matches[0]
        if c.get('slug')!='rbx-dns-renewal' or c.get('schedule'):raise ValueError('foreign_renewal_check')
        if any(c.get(k)!=v for k,v in desired.items() if k not in ('channels','unique')) or not c.get('channels'):
            c=h.api(api_key,c['update_url'],desired)
    else:c=h.api(api_key,h.API,desired)
    entry='rbx/healthchecks/dns-renewal'
    if (STORE/(entry+'.gpg')).exists():
        if h.stored(entry)!=c['ping_url']:raise ValueError('existing_ping_differs')
    else:
        subprocess.run(['pass','insert','--multiline',entry],input=(c['ping_url']+'\n').encode(),capture_output=True,check=True,timeout=30)
    after=h.api(api_key)['checks']
    prior={x['name']:x for x in before if x['name']!='RBX DNS renewal'}
    for x in after:
        if x['name'] in prior:
            assert all(x.get(k)==prior[x['name']].get(k) for k in ['uuid','name','slug','timeout','grace','channels'])
    subprocess.run(['git','-C',str(STORE),'push'],capture_output=True,check=True,timeout=45)
    branch=subprocess.check_output(['git','-C',str(STORE),'branch','--show-current'],text=True).strip()
    bare=CACHE/'encrypted-remote-fetch.git'
    subprocess.run(['git','init','--bare',str(bare)],capture_output=True,check=True)
    url=subprocess.check_output(['git','-C',str(STORE),'remote','get-url','origin'],text=True).strip()
    subprocess.run(['git','-C',str(bare),'fetch','--depth=1',url,'refs/heads/'+branch],capture_output=True,check=True,timeout=45)
    cipher=subprocess.check_output(['git','-C',str(bare),'show','FETCH_HEAD:'+ENTRY+'.gpg'])
    recovered=subprocess.run(['gpg','--batch','--pinentry-mode','error','--decrypt'],input=cipher,capture_output=True,check=True,timeout=30).stdout
    restored=serialization.load_ssh_private_key(recovered,password=None)
    assert restored.public_key().public_bytes(serialization.Encoding.OpenSSH,serialization.PublicFormat.OpenSSH)==public.split(b' rbx-dns-pilot-publisher')[0]
    receipt={'publisher_key_created':created,'publisher_entry':ENTRY,'fresh_remote_ciphertext_restored':True,
             'same_public_key':True,'publisher_cipher_sha256':hashlib.sha256(cipher).hexdigest(),
             'renewal_check':'RBX DNS renewal','period_seconds':3600,'grace_seconds':3600,
             'notification_channel_attached':bool(c.get('channels')),'other_checks_preserved':len(prior),
             'private_plaintext_persistent_file_written':False,'new_paid_plan':False}
    (CACHE/'custody.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt))
except Exception as error:
    print(json.dumps({'completed':False,'error_type':type(error).__name__}));raise SystemExit(1)
