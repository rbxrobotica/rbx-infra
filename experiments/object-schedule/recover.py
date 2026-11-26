#!/usr/bin/env python3
"""Owner-run workload receipt verification and network-isolated plaintext recovery."""
import argparse
import base64
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import time
import runtime


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--receipt',type=Path,required=True)
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--public-key',type=Path,required=True)
    p.add_argument('--image',required=True)
    p.add_argument('--markdown-package',type=Path,required=True)
    p.add_argument('--mdurl-package',type=Path,required=True)
    args=p.parse_args()
    # Restoration of historical snapshots must remain possible after freshness expires.
    signed=runtime.load(args.receipt); value=json.loads(signed)
    receipt=runtime.verify_receipt(signed,base64.b64decode(runtime.load(args.public_key)),
                                   value['finished_epoch'],max_age=0)
    cipher=runtime.load(args.archive)
    if len(cipher)!=receipt['ciphertext_bytes'] or runtime.digest(cipher)!=receipt['ciphertext_sha256']:
        raise ValueError('cipher_readback_mismatch')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}',args.image):raise ValueError('pinned_restore_image_required')
    script=Path(__file__).parent.parent/'object-corpus/archive.py'
    spec=importlib.util.spec_from_file_location('archive',script)
    archive=importlib.util.module_from_spec(spec);spec.loader.exec_module(archive)
    started=time.monotonic();raw=archive.gpg_decrypt(cipher)
    command=['podman','run','--rm','--interactive','--pull=never','--http-proxy=false',
        '--network=none','--read-only','--cap-drop=all','--security-opt=no-new-privileges',
        '--user=65534:65534','--memory=256m','--pids-limit=32',
        '--tmpfs=/restore:rw,noexec,nosuid,nodev,size=16m,mode=1777',
        '--mount',f'type=bind,src={script.resolve()},dst=/app/archive.py,ro',
        '--mount',f'type=bind,src={args.markdown_package.resolve()},dst=/deps/markdown_it,ro',
        '--mount',f'type=bind,src={args.mdurl_package.resolve()},dst=/deps/mdurl,ro',
        '--env=PYTHONPATH=/deps',args.image,'python3','-B','/app/archive.py','sandbox',
        '--manifest-sha',receipt['manifest_sha256']]
    result=subprocess.run(command,input=raw,capture_output=True,timeout=90)
    if result.returncode:raise ValueError('isolated_restore_failed')
    report=json.loads(result.stdout)
    report.update(workload_signature_verified=True,ciphertext_sha256=receipt['ciphertext_sha256'],
        restore_phase_seconds=round(time.monotonic()-started,3),owner_private_key_in_sandbox=False,
        full_service_rto_established=False)
    print(json.dumps(report))


if __name__=='__main__':
    try:main()
    except Exception as e:
        print(json.dumps({'restored':False,'error_type':type(e).__name__}));raise SystemExit(1)
