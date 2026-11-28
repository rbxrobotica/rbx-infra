#!/usr/bin/env python3
"""Bounded local synthetic cryptographic acceptance; no operational inputs."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile

import kit_package as k


def main():
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='rbx-synthetic-crypto-', dir=os.environ['RBX_DISPOSABLE_CACHE_ROOT']) as tmp:
        root = Path(tmp); source = root/'source'; source.mkdir(mode=0o700)
        fresh = root/'restore'; fresh.mkdir(mode=0o700)
        try:
            parameters = (b'Key-Type: RSA\nKey-Length: 2048\nKey-Usage: encrypt\n'
                b'Name-Real: RBX synthetic offline recovery test\nExpire-Date: 1d\n%no-protection\n%commit\n')
            k.gpg(source, ['--generate-key'], parameters)
            public, _ = k.gpg(source, ['--with-colons', '--list-keys'])
            identity = next(x.split(b':')[9].decode() for x in public.splitlines() if x.startswith(b'fpr:'))
            public, _ = k.gpg(source, ['--export', identity])
            private, _ = k.gpg(source, ['--export-secret-keys', identity])
            clear = b'Synthetic acceptance value; no real credential.\n'
            ciphertext, _ = k.gpg(source, ['--trust-model', 'always', '--recipient', identity, '--encrypt'], clear)
            store = root/'store'; store.mkdir(mode=0o700)
            k.write_new(store/'entry.gpg', ciphertext); k.write_new(store/'.gpg-id', (identity+'\n').encode())
            base = ['git', '-C', str(store)]
            k.checked(base+['init', '-b', 'main']); k.checked(base+['add', '.'])
            k.checked(base+['-c', 'user.name=Synthetic test', '-c', 'user.email=synthetic@example.invalid', 'commit', '-m', 'Synthetic only'])
            bundle = root/'store.bundle'; k.checked(base+['bundle', 'create', str(bundle), '--all'])
            history, commits = k.history_blobs(store)
            files = {'private-key.asc': private, 'public-certificate.gpg': public,
                'recovery-canary.gpg': ciphertext, 'password-store.bundle': bundle.read_bytes(),
                'RECOVERY.md': b'# Synthetic only\n', **k.current_members(store), **history}
            manifest = {'schema': k.SCHEMA, 'store_head': k.checked(base+['rev-parse', 'HEAD']).decode().strip(),
                'store_commits': commits, 'current_entries': 1, 'historical_ciphertext_blobs': len(history),
                'canary': k.digest(clear), 'files': {n: k.digest(v) for n, v in files.items()}}
            archive = k.archive(files, manifest)
            phrase = b'Synthetic test factor only, never an operational secret.'
            closed, _ = k.gpg(source, ['--cipher-algo', 'AES256', '--s2k-mode', '3',
                '--s2k-digest-algo', 'SHA512', '--s2k-count', '65011712', '--compress-algo', 'none',
                '--output', '-', '--symmetric'], archive, phrase)
            opened, _ = k.gpg(fresh, ['--output', '-', '--decrypt'], closed, phrase)
            members, recovered = k.unpack(opened)
            assert members == files and recovered == manifest
            failures = 0
            for bad, factor in ((closed, b'Incorrect synthetic factor, not a real secret.'),
                                (closed[:-17], phrase), (closed[:64]+b'corrupt'+closed[71:], phrase)):
                try:
                    k.gpg(fresh, ['--output', '-', '--decrypt'], bad, factor)
                except ValueError:
                    failures += 1
            assert failures == 3
            k.gpg(fresh, ['--import'], members['private-key.asc'])
            count = 0
            for name, body in members.items():
                if name.endswith('.gpg') and name.startswith(('current/', 'history/')):
                    value, _ = k.gpg(fresh, ['--output', '-', '--decrypt'], body)
                    assert value == clear
                    count += 1
            restored = root/'recovered.bundle'; k.write_new(restored, members['password-store.bundle'])
            repo = root/'recovered.git'; k.checked(['git', 'clone', '--mirror', str(restored), str(repo)])
            k.checked(['git', '-C', str(repo), 'fsck', '--full'])
            assert k.history_blobs(repo)[0] == history
            print(json.dumps({'synthetic_crypto': 'passed', 'archive_roundtrip': True,
                'wrong_factor_tamper_truncation_rejected': failures, 'authenticated_ciphertext_decryptions': count,
                'bundle_reconstruction_and_history_reconciliation': True, 'operational_inputs_accessed': False}))
        finally:
            k.stop_agent(source); k.stop_agent(fresh)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'synthetic_crypto': 'failed', 'error_type': type(error).__name__}))
        raise SystemExit(1)
