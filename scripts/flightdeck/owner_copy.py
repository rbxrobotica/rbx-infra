#!/usr/bin/env python3
"""Keep an independently downloaded encrypted FlightDeck copy on the owner host."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from plausible.s3_archive import ArchiveClient, Config, ArchiveError  # noqa: E402


def credential(entry):
    result=subprocess.run(['pass','show',entry],capture_output=True,timeout=20)
    if result.returncode or not 1<=len(result.stdout)<=4096:raise RuntimeError('Existing S3 credential unavailable')
    return result.stdout.decode().strip()


def preserve(client, directory, *, now=None):
    now=now or dt.datetime.now(dt.timezone.utc)
    latest=client.latest('full',3)
    if latest.get('status')=='unknown' or latest.get('age_seconds',93601)>93600:
        raise RuntimeError('FlightDeck has no full archive verified within 26 hours')
    stamp=now.strftime('%Y%m%dT%H%M%S.%fZ')
    destination=directory/(stamp+'.owner-copy.tar.gpg')
    proof=client.download(latest['key'],destination,latest['sha256'],latest['version_id'])
    if not proof.get('verified'):raise RuntimeError('Exact-version readback not proved')
    digest=hashlib.sha256(destination.read_bytes()).hexdigest()
    if digest!=latest['sha256']:raise RuntimeError('Independent encrypted copy hash differs')
    receipt={'schema':'rbx.flightdeck.owner-copy.v1','verified_at':now.isoformat(),
             'archive':proof,'source_age_seconds':latest['age_seconds'],
             'local_ciphertext':destination.name,'sha256':digest,
             'restore_tested':False,'source_host_or_cluster_storage':False}
    with (directory/(stamp+'.owner-copy-proof.json')).open('x') as stream:
        json.dump(receipt,stream,indent=2,sort_keys=True);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    return receipt


def main():
    os.umask(0o077)
    directory=Path.home()/'.local/share/rbx-backups/flightdeck'
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    if directory.is_symlink() or directory.stat().st_mode & 0o077:
        raise RuntimeError('Owner backup directory must be private')
    client=ArchiveClient(Config(endpoint='https://eu2.contabostorage.com',bucket='rbx-data-lake',
        region='eu-central-1',access_key=credential('rbx/s3/access-key'),
        secret_key=credential('rbx/s3/secret-key'),scope='flightdeck',max_bytes=64*1024*1024))
    proof=preserve(client,directory)
    print(json.dumps({'state':'VERIFIED_ENCRYPTED_OWNER_COPY','source_age_seconds':proof['source_age_seconds'],
                      'copy':proof['local_ciphertext'],'restore_tested':False}))


if __name__=='__main__':
    try:main()
    except (ArchiveError,RuntimeError,OSError,ValueError,KeyError,subprocess.SubprocessError):
        print('FlightDeck owner copy failed; inspect archive age and existing access. Upstream output suppressed.',file=sys.stderr)
        sys.exit(1)
