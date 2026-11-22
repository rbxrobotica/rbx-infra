#!/usr/bin/env python3
"""Closed owner-recovery capsule. No decrypted credential is printed or stored.

The live seal requires a no-swap cgroup and an owner-private source specification.
Restore is separately launched in a networkless, masked-home no-swap sandbox.
Only ciphertext may cross hosts or be written to media. IRC cannot launch this.
"""
import argparse
import datetime
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import resource
import stat
import subprocess
import tarfile
import tempfile
import time

SCHEMA = 'rbx.complete-owner-recovery.v1'
MAX_MEMBER = 16 * 1024 * 1024
MAX_ARCHIVE = 128 * 1024 * 1024
MAX_MEMBERS = 10000


class GpgFailure(ValueError):
    def __init__(self, statuses, returncode, numeric_errors=()):
        super().__init__('gpg_operation_failed')
        # Only fixed protocol keywords and a process return code; never status
        # arguments, human diagnostics, key IDs, paths or decrypted output.
        allowed = {'BAD_PASSPHRASE', 'MISSING_PASSPHRASE', 'DECRYPTION_FAILED',
                   'BADMDC', 'NODATA', 'NO_SECKEY', 'ERROR', 'FAILURE', 'IMPORT_OK',
                   'IMPORT_PROBLEM', 'DECRYPTION_OKAY', 'GOODMDC', 'END_DECRYPTION'}
        self.diagnostic = {'status_tags': sorted(statuses & allowed), 'process_exit': returncode,
                           'numeric_error_codes': sorted(set(numeric_errors))}


def phase(name):
    print(json.dumps({'phase': name}), flush=True)


def digest(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def safe_name(name):
    if not isinstance(name, str) or len(name.encode('utf-8')) > 4096:
        return False
    p = PurePosixPath(name)
    return (isinstance(name, str) and bool(p.parts) and not p.is_absolute()
            and name == '/'.join(p.parts) and all(x not in ('.', '..') for x in p.parts)
            and '\\' not in name and not any(ord(c) < 32 for c in name))


def read_owned(path, limit=MAX_MEMBER, private=True):
    path = Path(path)
    if path.resolve() != path:
        raise ValueError('symlink_source')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        s = os.fstat(fd)
        if (not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid()
                or (private and s.st_mode & 0o077) or not 0 < s.st_size <= limit):
            raise ValueError('unsafe_source_metadata')
        raw = bytearray()
        while len(raw) < s.st_size:
            chunk = os.read(fd, min(65536, s.st_size-len(raw)))
            if not chunk:
                raise ValueError('short_source')
            raw.extend(chunk)
        after = os.fstat(fd)
        if (s.st_size, s.st_mtime_ns, s.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('source_changed')
        return bytes(raw)
    finally:
        os.close(fd)


def write_new(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(raw); f.flush(); os.fsync(f.fileno())


def checked(args, data=None, timeout=60, fds=()):
    r = subprocess.run(args, input=data, capture_output=True, timeout=timeout,
                       pass_fds=fds, env={**os.environ, 'LC_ALL': 'C.UTF-8'})
    if r.returncode:
        raise ValueError('subprocess_failed')
    return r.stdout


def memory_gate():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    rel = Path('/proc/self/cgroup').read_text().split('0::', 1)[1].strip().lstrip('/')
    root = Path('/sys/fs/cgroup')/rel
    if (root/'memory.swap.max').read_text().strip() != '0':
        raise ValueError('no_swap_cgroup_required')
    limit = (root/'memory.max').read_text().strip()
    if limit == 'max' or not 256*1024*1024 <= int(limit) <= 1024*1024*1024:
        raise ValueError('bounded_memory_cgroup_required')


def archive(files, manifest):
    if set(files) != set(manifest['files']) or manifest.get('schema') != SCHEMA:
        raise ValueError('manifest_members_mismatch')
    if any(not allowed_member(n) or not 0 < len(v) <= MAX_MEMBER
           or digest(v) != manifest['files'][n] for n, v in files.items()):
        raise ValueError('invalid_member')
    validate_manifest(files, manifest)
    values = {**files, 'manifest.json': (json.dumps(manifest, sort_keys=True)+'\n').encode()}
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w', format=tarfile.GNU_FORMAT) as t:
        for name, body in sorted(values.items()):
            item = tarfile.TarInfo(name); item.size = len(body); item.mode = 0o600
            t.addfile(item, io.BytesIO(body))
    raw = out.getvalue()
    if len(raw) > MAX_ARCHIVE or len(values) > MAX_MEMBERS:
        raise ValueError('archive_bounds')
    return raw


def allowed_member(name):
    fixed = {'private-key.asc', 'public-certificate.gpg', 'recovery-canary.gpg',
             'password-store.bundle', 'RECOVERY.md',
             'tools/kit_package.py', 'tools/restore_sandbox.py'}
    return safe_name(name) and (name in fixed
        or re.fullmatch(r'history/[0-9a-f]{40}\.gpg', name) is not None
        or (name.startswith('current/') and (name.endswith('.gpg') or PurePosixPath(name).name == '.gpg-id')))


def validate_manifest(files, manifest):
    required = {'private-key.asc', 'public-certificate.gpg', 'recovery-canary.gpg',
                'password-store.bundle', 'RECOVERY.md', 'current/.gpg-id'}
    if (not required <= set(files) or not re.fullmatch('[0-9a-f]{40}', manifest.get('store_head', ''))
            or type(manifest.get('store_commits')) is not int or manifest['store_commits'] < 1):
        raise ValueError('incomplete_recovery_manifest')
    for field, prefix in (('current_entries', 'current/'), ('historical_ciphertext_blobs', 'history/')):
        count = sum(n.startswith(prefix) and n.endswith('.gpg') for n in files)
        if type(manifest.get(field)) is not int or count < 1 or manifest[field] != count:
            raise ValueError('recovery_scope_count_mismatch')
    canary = manifest.get('canary', {})
    if (type(canary.get('bytes')) is not int or not 0 < canary['bytes'] <= MAX_MEMBER
            or not re.fullmatch('[0-9a-f]{64}', canary.get('sha256', ''))):
        raise ValueError('invalid_canary_manifest')


def unpack(raw):
    if not 0 < len(raw) <= MAX_ARCHIVE:
        raise ValueError('archive_bounds')
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as t:
        members = t.getmembers()
        if (not 1 < len(members) <= MAX_MEMBERS or len({m.name for m in members}) != len(members)
                or any(not m.isfile() or not (m.name == 'manifest.json' or allowed_member(m.name))
                       or m.pax_headers or m.sparse is not None
                       or not 0 < m.size <= MAX_MEMBER for m in members)
                or sum(m.size for m in members) > MAX_ARCHIVE):
            raise ValueError('unsafe_archive_members')
        files = {m.name: t.extractfile(m).read() for m in members}
    manifest = json.loads(files.pop('manifest.json'))
    if (manifest.get('schema') != SCHEMA or set(files) != set(manifest['files'])
            or any(digest(v) != manifest['files'][n] for n, v in files.items())):
        raise ValueError('archive_integrity_mismatch')
    validate_manifest(files, manifest)
    return files, manifest


def gpg(home, args, raw=None, phrase=None, ask=False, timeout=60):
    command = ['/usr/bin/gpg', '--no-options', '--homedir', str(home), '--batch',
        '--no-tty', '--disable-dirmngr', '--auto-key-locate', 'clear',
        '--no-auto-key-retrieve', '--no-auto-key-import', '--no-symkey-cache', '--status-fd', '2']
    pipes = ()
    if phrase is not None:
        if not 16 <= len(phrase) <= 1024 or b'\n' in phrase or b'\r' in phrase:
            raise ValueError('invalid_outer_phrase_format')
        r, w = os.pipe()
        try:
            os.write(w, phrase+b'\n')
        finally:
            os.close(w)
        pipes = (r,)
        command += ['--pinentry-mode', 'loopback', '--passphrase-fd', str(r)]
    elif ask:
        command += ['--pinentry-mode', 'ask']
    try:
        result = subprocess.run(command+args, input=raw, capture_output=True,
                                timeout=timeout, pass_fds=pipes,
                                env={**os.environ, 'LC_ALL': 'C.UTF-8'})
    finally:
        for fd in pipes:
            os.close(fd)
    statuses = {x.split()[1].decode('ascii') for x in result.stderr.splitlines()
                if x.startswith(b'[GNUPG:] ') and len(x.split()) >= 2}
    if result.returncode or statuses & {'BADMDC', 'DECRYPTION_FAILED', 'ERROR', 'FAILURE', 'NO_SECKEY'}:
        errors = []
        for line in result.stderr.splitlines():
            match = re.fullmatch(rb'\[GNUPG:\] (?:ERROR|FAILURE) [A-Za-z0-9_.-]+ ([0-9]{1,10})', line)
            if match and int(match[1]) < 2**32:
                errors.append(int(match[1]))
        raise GpgFailure(statuses, result.returncode, errors)
    if '--decrypt' in args and not {'DECRYPTION_OKAY', 'GOODMDC', 'END_DECRYPTION'} <= statuses:
        raise ValueError('authenticated_decryption_required')
    return result.stdout, statuses


def stop_agent(home):
    checked(['/usr/bin/gpgconf', '--homedir', str(home), '--kill', 'gpg-agent'], timeout=10)


def current_members(store):
    files = {}
    for p in sorted(store.rglob('*')):
        if '.git' in p.relative_to(store).parts:
            continue
        if p.name.endswith('.gpg') or p.name == '.gpg-id':
            relative = p.relative_to(store).as_posix()
            if not safe_name(relative):
                raise ValueError('unexpected_store_path')
            files['current/'+relative] = read_owned(p, private=False)
    if not any(n.endswith('.gpg') for n in files):
        raise ValueError('empty_store')
    return files


def history_blobs(store):
    base = ['git', '-C', str(store)]
    heads = checked(base+['rev-list', '--all']).splitlines()
    blobs = set()
    for head in heads:
        for item in checked(base+['ls-tree', '-rz', '--full-tree', head.decode()]).split(b'\0'):
            if not item:
                continue
            meta, name = item.split(b'\t', 1)
            mode, kind, oid = meta.split()
            if name.endswith(b'.gpg'):
                if kind != b'blob' or mode not in (b'100644', b'100755'):
                    raise ValueError('unexpected_historical_entry')
                blobs.add(oid.decode('ascii'))
    return {'history/'+oid+'.gpg': checked(base+['cat-file', 'blob', oid]) for oid in sorted(blobs)}, len(heads)


def seal(spec_path, destination):
    memory_gate()
    spec = json.loads(read_owned(spec_path))
    if (destination.resolve() != destination or destination.stat().st_uid != os.getuid()
            or destination.stat().st_mode & 0o077 or any(destination.iterdir())):
        raise ValueError('empty_private_destination_required')
    store = Path(spec['store'])
    if store.resolve() != store or store.stat().st_uid != os.getuid() or store.stat().st_mode & 0o077:
        raise ValueError('unsafe_store')
    base = ['git', '-C', str(store)]
    head = checked(base+['rev-parse', 'HEAD']).decode().strip()
    remote = checked(base+['rev-parse', 'origin/main']).decode().strip()
    advertised = checked(base+['ls-remote', '--exit-code', 'origin', 'refs/heads/main']).split()[0].decode()
    if head != remote or head != advertised or checked(base+['diff', '--name-only', 'HEAD']):
        raise ValueError('unpublished_tracked_snapshot')
    files = current_members(store)
    history, commits = history_blobs(store)
    files.update(history)
    prepared = json.loads(read_owned(Path(spec['prepared_manifest'])))
    for member in ('private-key.asc', 'public-certificate.gpg', 'recovery-canary.gpg'):
        body = read_owned(Path(spec[member]))
        if digest(body) != prepared['files'][member]:
            raise ValueError('prepared_key_material_changed')
        files[member] = body
    # The bundle is itself an encrypted-entry Git repository, never a raw-key tar.
    with tempfile.TemporaryDirectory(dir=destination, prefix='bundle-stage-') as temp:
        bundle = Path(temp)/'password-store.bundle'
        checked(base+['bundle', 'create', str(bundle), '--all'])
        files['password-store.bundle'] = read_owned(bundle)
    if files.keys() != {**current_members(store), **history, **{n: files[n] for n in
        ('private-key.asc', 'public-certificate.gpg', 'recovery-canary.gpg', 'password-store.bundle')}}.keys():
        raise ValueError('source_set_changed')
    if any(files[n] != v for n, v in current_members(store).items()) or head != checked(base+['rev-parse', 'HEAD']).decode().strip():
        raise ValueError('source_changed')
    created = datetime.datetime.now(datetime.timezone.utc).isoformat()
    tools = Path(__file__).resolve().parent
    files['RECOVERY.md'] = read_owned(tools/'RECOVERY.md')
    for name in ('kit_package.py', 'restore_sandbox.py'):
        files['tools/'+name] = read_owned(tools/name)
    manifest = {'schema': SCHEMA, 'created_utc': created, 'store_head': head,
        'store_commits': commits, 'current_entries': sum(n.startswith('current/') and n.endswith('.gpg') for n in files),
        'historical_ciphertext_blobs': len(history), 'canary': {'bytes': prepared['canary_plaintext_bytes'],
        'sha256': prepared['canary_plaintext_sha256']}, 'files': {n: digest(v) for n, v in files.items()},
        'scope': 'all current .gpg and .gpg-id files plus every retained Git .gpg blob; noncipher untracked files excluded'}
    raw = archive(files, manifest)
    phrase = checked(['/usr/bin/pass', 'show', 'rbx/external-backup-kit/pass'], timeout=315).splitlines()[0]
    with tempfile.TemporaryDirectory(dir='/dev/shm', prefix='rbx-seal-') as tmp:
        try:
            cipher, _ = gpg(tmp, ['--cipher-algo', 'AES256', '--s2k-mode', '3', '--s2k-digest-algo', 'SHA512',
                '--s2k-count', '65011712', '--compress-algo', 'none', '--output', '-', '--symmetric'], raw, phrase)
            check, _ = gpg(tmp, ['--output', '-', '--decrypt'], cipher, phrase)
            unpack(check)
            if check != raw:
                raise ValueError('sealed_roundtrip_mismatch')
        finally:
            stop_agent(tmp)
    del phrase, raw, files
    write_new(destination/'recovery-kit.tar.gpg', cipher)
    receipt = {'result': 'sealed_outer_roundtrip_passed', 'ciphertext': digest(cipher),
        'created_utc': created, 'store_head': head, 'current_entries': manifest['current_entries'],
        'historical_ciphertext_blobs': len(history), 'store_commits': commits,
        'no_swap_cgroup': True, 'unwrapped_key_archive_persisted': False,
        'complete_snapshot_authenticated_decryption_tested': False}
    write_new(destination/'seal-receipt.json', (json.dumps(receipt, indent=2)+'\n').encode())
    return receipt


def native_phrase():
    def esc(s):
        return s.replace('%', '%25').replace('\n', '%0A').replace('\r', '%0D')
    text = 'Digite a frase externa do kit RBX a partir do registro físico independente. Não use o pass operacional neste ensaio.'
    request = ('SETTITLE RBX | Restaurar kit completo\nSETDESC '+esc(text)+
               '\nSETPROMPT Frase externa:\nSETTIMEOUT 600\nGETPIN\nBYE\n').encode()
    output = checked(['/usr/bin/pinentry-gnome3', '--display', os.environ['DISPLAY']], request, timeout=615)
    lines = [x[2:] for x in output.splitlines() if x.startswith(b'D ')]
    if len(lines) != 1:
        raise ValueError('native_pinentry_cancelled')
    return re.sub(rb'%([0-9A-Fa-f]{2})', lambda m: bytes([int(m[1], 16)]), lines[0])


def restore(cipher_path, expected_hash):
    phase('sandbox_preflight')
    memory_gate()
    if {x.split(':')[0].strip() for x in Path('/proc/net/dev').read_text().splitlines()[2:]} != {'lo'}:
        raise ValueError('networkless_sandbox_required')
    if any(any(p.iterdir()) for p in (Path.home()/'.gnupg', Path.home()/'.password-store')):
        raise ValueError('operational_credential_dirs_not_masked')
    # The outer file is authenticated ciphertext. exFAT permissions may be
    # synthesized as world-readable; private source keys/specs keep strict modes.
    cipher = read_owned(cipher_path, MAX_ARCHIVE, private=False)
    if hashlib.sha256(cipher).hexdigest() != expected_hash:
        raise ValueError('ciphertext_digest_mismatch')
    started = time.monotonic()
    phase('waiting_outer_unlock')
    phrase = native_phrase()
    with tempfile.TemporaryDirectory(dir='/dev/shm', prefix='rbx-restore-') as temp:
        root = Path(temp); home = root/'gpg'; home.mkdir(mode=0o700)
        try:
            phase('authenticate_outer_archive')
            clear, _ = gpg(home, ['--output', '-', '--decrypt'], cipher, phrase)
            del phrase
            files, manifest = unpack(clear); del clear
            (home/'gpg-agent.conf').write_text('pinentry-program /usr/bin/pinentry-gnome3\nno-allow-external-cache\n')
            stop_agent(home)
            phase('import_public_certificate')
            gpg(home, ['--import'], files['public-certificate.gpg'])
            phase('import_private_key_into_fresh_agent')
            gpg(home, ['--import'], files['private-key.asc'], ask=True, timeout=615)
            phase('waiting_inner_unlock_and_canary')
            canary, _ = gpg(home, ['--output', '-', '--decrypt'], files['recovery-canary.gpg'], ask=True, timeout=615)
            if digest(canary) != manifest['canary']:
                raise ValueError('canary_mismatch')
            del canary
            phase('authenticate_complete_current_and_historical_snapshot')
            count = 0
            for name, body in sorted(files.items()):
                if name.endswith('.gpg') and name.startswith(('current/', 'history/')):
                    value, _ = gpg(home, ['--output', '-', '--decrypt'], body, ask=True, timeout=615)
                    del value
                    count += 1
            bundle = root/'password-store.bundle'; write_new(bundle, files['password-store.bundle'])
            phase('reconstruct_and_reconcile_git_history')
            repository = root/'restored.git'
            checked(['git', 'clone', '--mirror', str(bundle), str(repository)])
            checked(['git', '-C', str(repository), 'fsck', '--full'])
            if checked(['git', '-C', str(repository), 'rev-parse', 'refs/heads/main']).decode().strip() != manifest['store_head']:
                raise ValueError('restored_head_mismatch')
            if int(checked(['git', '-C', str(repository), 'rev-list', '--all', '--count'])) != manifest['store_commits']:
                raise ValueError('restored_history_mismatch')
            restored_history, _ = history_blobs(repository)
            if restored_history != {n: v for n, v in files.items() if n.startswith('history/')}:
                raise ValueError('retained_history_scope_mismatch')
            expected = manifest['current_entries']+manifest['historical_ciphertext_blobs']
            if count != expected:
                raise ValueError('restored_ciphertext_count_mismatch')
            receipt = {'result': 'passed_complete_owner_recovery', 'ciphertext': digest(cipher),
                'store_head': manifest['store_head'], 'current_entries': manifest['current_entries'],
                'historical_ciphertext_blobs': manifest['historical_ciphertext_blobs'],
                'authenticated_decryptions': count, 'store_commits': manifest['store_commits'],
                'isolated_agent': True, 'operational_credential_dirs_masked': True,
                'recovery_network_disabled': True, 'no_swap_cgroup': True,
                'bundle_fsck_passed': True, 'inner_canary_hash_matches': True,
                'duration_seconds': round(time.monotonic()-started, 3)}
        finally:
            stop_agent(home)
    receipt['temporary_keyring_removed'] = not Path(temp).exists()
    return receipt


if __name__ == '__main__':
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest='operation', required=True)
    s = subs.add_parser('seal'); s.add_argument('--source-spec', type=Path, required=True)
    s.add_argument('--destination', type=Path, required=True)
    r = subs.add_parser('restore'); r.add_argument('--ciphertext', type=Path, required=True)
    r.add_argument('--sha256', required=True)
    a = parser.parse_args()
    try:
        if a.operation == 'seal':
            answer = seal(a.source_spec, a.destination)
        else:
            answer = restore(a.ciphertext, a.sha256)
        print(json.dumps(answer))
    except Exception as e:
        print(json.dumps({'result': 'failed', 'error_type': type(e).__name__,
                          'failure': str(e) if isinstance(e, ValueError) else 'system_operation_failed',
                          **({'gpg_diagnostic': e.diagnostic} if isinstance(e, GpgFailure) else {})}))
        raise SystemExit(1)
