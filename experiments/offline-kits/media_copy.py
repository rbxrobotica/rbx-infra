#!/usr/bin/env python3
"""Add one closed generation to one privately bound, capacity-qualified USB.

Never removes files or overwrites a generation. Pinned directory descriptors
keep all writes/readback on the selected filesystem. A full restore is separate.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat

import capacity as c
import kit_package as k


def publish_locked(role, generation, capacity_receipt, state_directory):
    os.umask(0o077)
    if (capacity_receipt.parent.parent != c.BASE
            or not re.fullmatch('capacity-'+role+'-[0-9a-f]{32}', capacity_receipt.parent.name)
            or capacity_receipt.name != 'status.json'):
        raise ValueError('unexpected_capacity_receipt')
    proof_raw = k.read_owned(capacity_receipt)
    proof = json.loads(proof_raw)
    if (proof.get('role') != c.ROLES[role][0]
            or proof.get('result') != 'passed_file_capacity_and_integrity_test'
            or proof.get('phase') != 'finished' or proof.get('only_owned_test_files_removed') is not True
            or proof.get('all_written_pages_nonresident_before_read') is not True
            or type(proof.get('tested_bytes')) is not int
            or proof['tested_bytes'] < 60_000_000_000 or proof['tested_bytes'] % 512):
        raise ValueError('capacity_qualification_required')
    counters = proof.get('counters', {})
    if any(type(v) is not int for v in counters.values()) or counters != {'Data OK': proof['tested_bytes']//512, 'Data LOST': 0,
                    'Corrupted': 0, 'Slightly changed': 0, 'Overwritten': 0}:
        raise ValueError('capacity_counters_not_reconciled')
    receipt = json.loads(k.read_owned(generation/'seal-receipt.json'))
    cipher = k.read_owned(generation/'recovery-kit.tar.gpg', k.MAX_ARCHIVE)
    if receipt.get('result') != 'sealed_outer_roundtrip_passed' or k.digest(cipher) != receipt['ciphertext']:
        raise ValueError('closed_generation_mismatch')
    if state_directory.resolve() != state_directory or any(state_directory.iterdir()):
        raise ValueError('fresh_private_readback_directory_required')
    if state_directory.stat().st_uid != os.getuid() or state_directory.stat().st_mode & 0o077:
        raise ValueError('private_readback_directory_required')
    files = {'recovery-kit.tar.gpg': cipher,
        'seal-receipt.json': (json.dumps(receipt, indent=2)+'\n').encode(),
        'OPEN-FIRST.md': ("# RBX encrypted recovery generation\n"
            "Keep this drive disconnected and protected. Its outer phrase and the GPG-key passphrase are separate physical factors.\n"
            "Use a trusted, reviewed recovery program from rbx-infra PR416 on a clean system. Never execute media-supplied programs blindly.\n"
            "The ciphertext digest must match the protected generation record. SHA256SUMS on this drive alone is not an independent trust anchor.\n"
            "Decrypt with the trusted system GPG in a protected memory-only environment. Inspect the archive before any extraction.\n"
            "The complete package holds the current encrypted vault, retained history, existing protected GPG key, certificate and canary.\n"
            "Its presence does not prove account-factor validity, founder-independent access or all company-data backup coverage.\n").encode()}
    files['SHA256SUMS'] = ''.join(hashlib.sha256(v).hexdigest()+'  '+n+'\n'
                                  for n, v in sorted(files.items())).encode()
    mount, device = c.device(role)
    root = os.open(mount, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    directory = 'RBX-RECOVERY-20261122-'+receipt['ciphertext']['sha256'][:16]
    destination = None
    try:
        if os.fstat(root).st_dev != device or os.fstatvfs(root).f_bavail*os.fstatvfs(root).f_frsize < sum(map(len, files.values()))+10*1024*1024:
            raise ValueError('media_identity_or_free_space_changed')
        os.mkdir(directory, mode=0o700, dir_fd=root)
        destination = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        directory_identity = os.fstat(destination).st_ino
        identities = {}
        for name, raw in files.items():
            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=destination)
            try:
                with os.fdopen(os.dup(fd), 'wb') as out:
                    out.write(raw); out.flush(); os.fsync(out.fileno())
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_dev != device or info.st_nlink != 1:
                    raise ValueError('unexpected_copied_file')
                c.evict_and_verify(fd, len(raw))
                os.lseek(fd, 0, os.SEEK_SET)
                result = bytearray()
                while len(result) < len(raw):
                    chunk = os.read(fd, min(65536, len(raw)-len(result)))
                    if not chunk:
                        raise ValueError('short_physical_readback')
                    result.extend(chunk)
                if bytes(result) != raw:
                    raise ValueError('physical_readback_mismatch')
                identities[name] = (info.st_ino, info.st_size, info.st_mtime_ns)
                named = os.stat(name, dir_fd=destination, follow_symlinks=False)
                if (not stat.S_ISREG(named.st_mode) or named.st_nlink != 1
                        or (named.st_ino, named.st_size, named.st_mtime_ns) != identities[name]):
                    raise ValueError('named_media_file_changed')
                if name == 'recovery-kit.tar.gpg':
                    k.write_new(state_directory/'readback.tar.gpg', bytes(result))
            finally:
                os.close(fd)
        os.fsync(destination); os.fsync(root)
        if set(os.listdir(destination)) != set(files):
            raise ValueError('generation_members_changed')
        for name, expected in identities.items():
            named = os.stat(name, dir_fd=destination, follow_symlinks=False)
            if (not stat.S_ISREG(named.st_mode) or named.st_nlink != 1
                    or (named.st_ino, named.st_size, named.st_mtime_ns) != expected):
                raise ValueError('named_media_file_changed')
        named_directory = os.stat(directory, dir_fd=root, follow_symlinks=False)
        if not stat.S_ISDIR(named_directory.st_mode) or named_directory.st_ino != directory_identity:
            raise ValueError('named_generation_changed')
        if c.device(role)[1] != device:
            raise ValueError('media_identity_changed_after_readback')
        answer = {'role': c.ROLES[role][0], 'result': 'passed_physical_ciphertext_readback',
            'generation_directory': directory, 'ciphertext': receipt['ciphertext'],
            'all_files_flushed_and_nonresident_before_readback': True,
            'capacity_receipt_sha256': hashlib.sha256(proof_raw).hexdigest(),
            'preexisting_files_changed_or_removed': False, 'complete_restore_from_this_readback': False,
            'direct_thinkcentre_usb_read_or_power_cycle_tested': False}
        k.write_new(state_directory/'media-copy-receipt.json', (json.dumps(answer, indent=2)+'\n').encode())
        return answer
    finally:
        if destination is not None:
            os.close(destination)
        os.close(root)


def publish(role, generation, capacity_receipt, state_directory):
    lock = os.open(c.BASE/('capacity-'+role+'.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return publish_locked(role, generation, capacity_receipt, state_directory)
    finally:
        os.close(lock)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--role', choices=c.ROLES, required=True)
    parser.add_argument('--generation', type=Path, required=True)
    parser.add_argument('--capacity-receipt', type=Path, required=True)
    parser.add_argument('--readback-directory', type=Path, required=True)
    a = parser.parse_args()
    try:
        print(json.dumps(publish(a.role, a.generation, a.capacity_receipt, a.readback_directory)))
    except Exception as error:
        print(json.dumps({'result': 'failed_preserving_files', 'error_type': type(error).__name__,
            'failure': str(error) if isinstance(error, ValueError) else 'system_operation_failed'}))
        raise SystemExit(1)
