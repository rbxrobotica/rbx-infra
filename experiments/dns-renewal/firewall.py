#!/usr/bin/env python3
"""Declare/remove only the owned isolated origin's exact IPv4 port-53 chain."""
import subprocess
import sys

ADDRESS = '161.97.147.76'
PARTNERS = ['109.201.133.251','178.156.179.118','51.91.57.244','185.206.180.55',
            '45.83.248.12','45.83.249.12','45.83.250.12','45.83.251.12','185.206.180.204']
CHAIN, TAG = 'RBX_DNS_RENEW', 'rbx-dns-renewal-pilot'

def command(*args, check=True):
    return subprocess.run(['/usr/sbin/iptables','-w','5',*args],capture_output=True,check=check,timeout=15)

def rules():
    return [['-s',ip,'-m','comment','--comment',TAG,'-j','ACCEPT'] for ip in PARTNERS+[ADDRESS,'127.0.0.1']] + [['-m','comment','--comment',TAG,'-j','DROP']]

def jump(protocol):
    return ['INPUT','-d',ADDRESS,'-p',protocol,'--dport','53','-m','comment','--comment',TAG,'-j',CHAIN]

def main(action):
    exists=command('-S',CHAIN,check=False).returncode==0
    if exists:
        # Refuse foreign rules, including legitimate operator additions.
        for line in command('-S',CHAIN).stdout.decode().splitlines():
            if line != '-N '+CHAIN and TAG not in line:
                raise ValueError('foreign_chain_rule')
    if action=='start':
        if exists:
            raise ValueError('existing_chain_requires_review')
        command('-N',CHAIN)
        try:
            for rule in rules(): command('-A',CHAIN,*rule)
            for protocol in ('tcp','udp'):
                j=jump(protocol);command('-I',j[0],'1',*j[1:])
        except Exception:
            main('stop');raise
    elif action=='stop':
        for protocol in ('tcp','udp'):
            j=jump(protocol)
            if command('-C',*j,check=False).returncode==0: command('-D',*j)
        if exists: command('-F',CHAIN);command('-X',CHAIN)
    else: raise ValueError('action')

if __name__=='__main__': main(sys.argv[1])
