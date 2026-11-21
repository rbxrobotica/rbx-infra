"""Pinned health across legitimate signature/serial renewals, without content drift."""
import concurrent.futures
import copy
import time

import dns.dnssec
import dns.flags
import dns.message
import dns.query
import dns.rdatatype

import core

SERVERS=['45.83.248.12','45.83.249.12','45.83.250.12','45.83.251.12']
CASES=[(core.ORIGIN,'DNSKEY'),(core.ORIGIN,'SOA'),('_canary.'+core.ORIGIN,'TXT')]


def evaluate(response, owner, kind, baseline, floor, warning, now=None):
    now = time.time() if now is None else now
    name=dns.name.from_text(owner);rdtype=dns.rdatatype.from_text(kind)
    expected=baseline.get_rdataset(name,rdtype)
    answer=[rr for rr in response.answer if rr.name==name and rr.rdtype==rdtype]
    signatures=[rr for rr in response.answer if rr.name==name and rr.rdtype==dns.rdatatype.RRSIG and rr.covers==rdtype]
    if len(response.question)!=1 or response.question[0].name!=name or response.question[0].rdtype!=rdtype:
        raise ValueError('question')
    if response.rcode()!=0 or not response.flags & dns.flags.AA or response.flags & dns.flags.TC:
        raise ValueError('answer_flags')
    if len(answer)!=1 or len(signatures)!=1 or len(signatures[0])!=1 or answer[0].ttl!=expected.ttl or signatures[0].ttl!=expected.ttl:
        raise ValueError('rrset_shape_or_ttl')
    observed_serial=None
    if kind=='SOA':
        if len(answer[0])!=1: raise ValueError('soa_shape')
        observed_serial=next(iter(answer[0])).serial
        if observed_serial!=floor and not core.newer(observed_serial,floor):
            raise ValueError('serial_rollback_or_ambiguous')
        a=[r.replace(serial=0).to_digestable(baseline.origin) for r in answer[0]]
        b=[r.replace(serial=0).to_digestable(baseline.origin) for r in expected]
    else:
        a=[r.to_digestable(baseline.origin) for r in answer[0]]
        b=[r.to_digestable(baseline.origin) for r in expected]
    if sorted(a)!=sorted(b): raise ValueError('data_changed')
    dns.dnssec.validate(answer[0],signatures[0],{baseline.origin:baseline.get_rdataset(core.ORIGIN,dns.rdatatype.DNSKEY)},now=now)
    sig=next(iter(signatures[0]))
    if sig.inception>now or sig.expiration<=now: raise ValueError('signature_time')
    if sig.expiration-now>core.LIFETIME+120:raise ValueError('signature_lifetime_exceeds_policy')
    return {'serial':observed_serial,'remaining_seconds':int(sig.expiration-now),
            'signature_expiration_epoch':sig.expiration,'warning':sig.expiration-now<=warning}


def run(baseline, floor, warning=86400, expected_serial=None, servers=SERVERS):
    def task(pair):
        server,tcp=pair;checks=[]
        for owner,kind in CASES:
            try:
                q=dns.message.make_query(owner,kind,want_dnssec=True,payload=1232);q.flags &= ~dns.flags.RD
                response=(dns.query.tcp if tcp else dns.query.udp)(q,server,timeout=2)
                c=evaluate(response,owner,kind,baseline,floor,warning)
                if expected_serial is not None and c['serial'] not in (None,expected_serial): raise ValueError('publication_not_converged')
                checks.append({'name':owner,'type':kind,'healthy':True,**c})
            except Exception as error:
                checks.append({'name':owner,'type':kind,'healthy':False,'error_type':type(error).__name__})
        return {'server':server,'tcp':tcp,'checks':checks}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        rows=list(pool.map(task,[(ip,tcp) for ip in servers for tcp in (False,True)]))
    checks=[c for row in rows for c in row['checks']]
    serials=sorted({c['serial'] for c in checks if c.get('serial') is not None})
    good=all(c['healthy'] for c in checks) and len(serials)==1
    return {'exit_code':2 if not good else 1 if any(c.get('warning') for c in checks) else 0,
            'checks':len(checks),'serials':serials,'observations':rows,
            'minimum_remaining_seconds':min((c['remaining_seconds'] for c in checks if c['healthy']),default=0)}
