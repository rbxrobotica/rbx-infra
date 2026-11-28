#!/usr/bin/env python3
"""File-only qualification of the two privately bound empty KAPBOM drives.

No raw-device writes, formatting, repair or deletion of pre-existing files.
Only a successfully checked, nonce-scoped test's own files may be removed.
Directory descriptors pin all F3 IO to the opened USB filesystem, including
when a device is removed or its mountpoint is reused.
"""
import argparse
import ctypes
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import time
import uuid

BASE = Path.home()/'.local/state/rbx-two-usb-kits-20261122'
TOOLS = Path.home()/'.local/share/rbx-recovery-tools/f3-8.0-2build2'
PINS = {'f3write': '850a853b8816fe5ab3f2b19815b13911ea3084055e23f5f9d233915bac99f1d2',
        'f3read': '3c1561112f74bfb18395b34edc52f393bc1c0ad3f2172f25044249441a8d83c9'}
ROLES = {'A': ('Kit A', 'KAPBOM KA-A-64', 62914560000),
         'B': ('Kit B', 'KAPBOM KA-G-64', 62930117632)}


def private(path):
    path = Path(path)
    s = path.lstat()
    if path.resolve() != path or s.st_uid != os.getuid() or s.st_mode & 0o077:
        raise ValueError('unsafe_private_path')
    return path


def atomic(path, value):
    raw = (json.dumps(value, sort_keys=True, indent=2)+'\n').encode()
    temp = path.with_name(path.name+'.new-'+uuid.uuid4().hex)
    fd = os.open(temp, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(raw); f.flush(); os.fsync(f.fileno())
    os.replace(temp, path)


def device(role):
    private(BASE)
    bindings = json.loads(private(BASE/'device-role-bindings.json').read_text())
    label, model, expected_size = ROLES[role]
    binding = bindings['roles'][label]
    if binding['model'] != model or not binding.get('serial'):
        raise ValueError('role_binding_drift')
    raw = subprocess.check_output(['lsblk', '-b', '-J', '-o',
        'NAME,PATH,TYPE,TRAN,RM,SIZE,SERIAL,FSTYPE,UUID,MOUNTPOINTS'], text=True)
    disks = [d for d in json.loads(raw)['blockdevices']
             if d.get('type') == 'disk' and d.get('tran') == 'usb'
             and d.get('rm') and d.get('serial') == binding['serial']]
    if len(disks) != 1 or disks[0]['size'] != expected_size:
        raise ValueError('usb_identity_or_size_mismatch')
    parts = [p for p in disks[0].get('children', []) if p.get('type') == 'part']
    if len(parts) != 1 or parts[0].get('fstype') != 'exfat':
        raise ValueError('unexpected_partition_layout')
    part = parts[0]
    if part.get('uuid') not in binding['filesystem_uuids']:
        raise ValueError('filesystem_identity_drift')
    mounts = [m for m in part.get('mountpoints', []) if m]
    if len(mounts) != 1:
        raise ValueError('expected_one_existing_mount')
    mount = Path(mounts[0])
    if mount.resolve() != mount or not mount.is_mount():
        raise ValueError('unsafe_or_missing_mount')
    if os.stat(mount).st_dev != os.stat(part['path']).st_rdev:
        raise ValueError('mount_is_not_the_bound_block_filesystem')
    return mount, os.stat(mount).st_dev


def files(fd):
    result = {}
    for name in os.listdir(fd):
        if not re.fullmatch(r'[1-9][0-9]*\.h2w', name):
            raise ValueError('unexpected_file_in_owned_test')
        s = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_size <= 0:
            raise ValueError('unexpected_test_file_type')
        result[name] = (s.st_ino, s.st_size, s.st_mtime_ns)
    if not 1 <= len(result) <= 128:
        raise ValueError('unexpected_test_file_count')
    return result


def evict_and_verify(fd, size):
    """Require zero resident file pages before F3's independent physical read.

    DONTNEED alone is advisory. mincore verifies that no previously written
    file-data pages remain resident; mapping the file does not fault its data.
    A later refill necessarily comes from the underlying filesystem/device.
    """
    if os.fstat(fd).st_uid != os.getuid():
        raise ValueError('cache_residency_requires_owned_file')
    os.fsync(fd)
    os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
                          ctypes.c_int, ctypes.c_int, ctypes.c_long]
    libc.mmap.restype = ctypes.c_void_p
    libc.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
    libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    address = libc.mmap(None, size, 1, 1, fd, 0)  # PROT_READ, MAP_SHARED
    if address == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_errno(), 'mmap failed')
    try:
        pages = (size+os.sysconf('SC_PAGE_SIZE')-1)//os.sysconf('SC_PAGE_SIZE')
        vector = (ctypes.c_ubyte*pages)()
        if libc.mincore(address, size, vector):
            raise OSError(ctypes.get_errno(), 'mincore failed')
        if any(x & 1 for x in vector):
            raise ValueError('written_pages_still_resident_refuse_cached_pass')
        return pages
    finally:
        libc.munmap(address, size)


def rename_exclusive(source_fd, name, target_fd, newname):
    libc = ctypes.CDLL(None, use_errno=True)
    libc.renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                               ctypes.c_char_p, ctypes.c_uint]
    if libc.renameat2(source_fd, name.encode(), target_fd, newname.encode(), 1):
        raise OSError(ctypes.get_errno(), 'exclusive cleanup quarantine failed')


def clean_owned(testfd, quarantinefd, expected):
    for name, identity in expected.items():
        captured = uuid.uuid4().hex+'.h2w'
        rename_exclusive(testfd, name, quarantinefd, captured)
        s = os.stat(captured, dir_fd=quarantinefd, follow_symlinks=False)
        if not stat.S_ISREG(s.st_mode) or (s.st_ino, s.st_size, s.st_mtime_ns) != identity:
            # Preserve the captured mismatch for inspection; never unlink it.
            raise ValueError('cleanup_identity_changed_preserved_in_quarantine')
        os.unlink(captured, dir_fd=quarantinefd)
    os.fsync(quarantinefd)


def parse_read(text, expected_bytes):
    # F3 can exit zero despite bad data; require all sector counters explicitly.
    counters = {}
    for name in ('Data OK', 'Data LOST', 'Corrupted', 'Slightly changed', 'Overwritten'):
        match = re.findall(re.escape(name)+r':[^\n]*\(([0-9]+) sectors\)', text)
        if len(match) != 1:
            raise ValueError('missing_or_ambiguous_f3_counter')
        counters[name] = int(match[0])
    if counters['Data OK']*512 != expected_bytes or any(
            counters[k] for k in counters if k != 'Data OK'):
        raise ValueError('f3_integrity_failure')
    return counters


def tool(name):
    p = TOOLS/name
    if p.resolve() != p or not p.is_file() or p.stat().st_uid != os.getuid():
        raise ValueError('unsafe_f3_tool')
    if hashlib.sha256(p.read_bytes()).hexdigest() != PINS[name]:
        raise ValueError('f3_binary_pin_mismatch')
    return str(p)


def command(name, fd, work, state, source_dev, role):
    log = work/(name+'.log')
    with log.open('xb') as out:
        child = subprocess.Popen([tool(name), '--show-progress=1', '/proc/self/fd/'+str(fd)],
            stdout=out, stderr=subprocess.STDOUT, pass_fds=(fd,),
            env={**os.environ, 'LC_ALL': 'C'}, start_new_session=True)
        start = time.monotonic()
        try:
            while child.poll() is None:
                _, current_dev = device(role)
                if current_dev != source_dev or os.fstat(fd).st_dev != source_dev:
                    raise ValueError('media_changed_during_test')
                state.update(updated_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                             phase=name, elapsed_seconds=round(time.monotonic()-start, 1),
                             live_test_file_bytes=sum(os.stat(n, dir_fd=fd, follow_symlinks=False).st_size
                                                      for n in os.listdir(fd)
                                                      if re.fullmatch(r'[1-9][0-9]*\.h2w', n)))
                atomic(work/'status.json', state)
                if time.monotonic()-start > 24*3600:
                    raise TimeoutError('capacity_phase_deadline')
                time.sleep(5)
            if child.returncode:
                raise ValueError('f3_process_failed')
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL); child.wait()
    return log.read_text(errors='replace')


def run(role, allow_cleanup):
    os.umask(0o077)
    label, model, _ = ROLES[role]
    mount, source_dev = device(role)
    lock = os.open(BASE/('capacity-'+role+'.lock'), os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
    work = BASE/('capacity-'+role+'-'+uuid.uuid4().hex)
    work.mkdir(mode=0o700)
    rootfd = os.open(mount, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    testfd = None
    quarantinefd = None
    state = {'role': label, 'model': model, 'result': 'running',
             'started_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
             'raw_device_writes': False, 'format_or_partition_change': False,
             'preexisting_file_deletion': False, 'own_test_cleanup_authorized': allow_cleanup}
    try:
        if os.fstat(rootfd).st_dev != source_dev or os.listdir(rootfd):
            raise ValueError('require_empty_pinned_usb_root')
        before = os.fstatvfs(rootfd)
        available = before.f_bavail*before.f_frsize
        if available < 60_000_000_000:
            raise ValueError('unexpected_free_capacity')
        dirname = '.rbx-capacity-'+uuid.uuid4().hex
        quarantine = '.rbx-test-cleanup-'+uuid.uuid4().hex
        os.mkdir(dirname, mode=0o700, dir_fd=rootfd)
        os.mkdir(quarantine, mode=0o700, dir_fd=rootfd)
        testfd = os.open(dirname, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW, dir_fd=rootfd)
        quarantinefd = os.open(quarantine, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW, dir_fd=rootfd)
        atomic(work/'ownership.json', {'test_directory': dirname, 'device': source_dev,
               'directory_inode': os.fstat(testfd).st_ino, 'cleanup_quarantine': quarantine,
               'role': label})
        state['free_bytes_before'] = available
        command('f3write', testfd, work, state, source_dev, role)
        expected = files(testfd)
        written = sum(s[1] for s in expected.values())
        remaining = os.fstatvfs(testfd)
        if written < available-2*1024*1024 or remaining.f_bavail*remaining.f_frsize > 2*1024*1024:
            raise ValueError('incomplete_free_space_coverage')
        # Flush only our files and VERIFY eviction before independent reading.
        evicted_pages = 0
        for name in expected:
            fd = os.open(name, os.O_RDONLY|os.O_NOFOLLOW, dir_fd=testfd)
            try:
                evicted_pages += evict_and_verify(fd, expected[name][1])
            finally:
                os.close(fd)
        counters = parse_read(command('f3read', testfd, work, state, source_dev, role), written)
        if files(testfd) != expected:
            raise ValueError('test_files_changed_after_verification')
        state.update(result='passed_file_capacity_and_integrity_test', tested_bytes=written,
                     counters=counters, all_written_pages_nonresident_before_read=True,
                     cache_pages_checked=evicted_pages,
                     power_cycle_or_long_term_retention_proven=False)
        if allow_cleanup:
            _, check_dev = device(role)
            if check_dev != source_dev or files(testfd) != expected:
                raise ValueError('refuse_cleanup_after_identity_or_file_change')
            clean_owned(testfd, quarantinefd, expected)
            os.fsync(testfd)
            os.close(testfd); testfd = None
            os.rmdir(dirname, dir_fd=rootfd); os.fsync(rootfd)
            os.close(quarantinefd); quarantinefd = None
            os.rmdir(quarantine, dir_fd=rootfd); os.fsync(rootfd)
            state['only_owned_test_files_removed'] = True
        else:
            state['only_owned_test_files_removed'] = False
        state.update(phase='finished', finished_at_utc=dt.datetime.now(dt.timezone.utc).isoformat())
    except Exception as error:
        state.update(result='failed', phase='stopped_preserving_artifacts', error_type=type(error).__name__,
                     failure=str(error) if isinstance(error, (ValueError, TimeoutError)) else 'system_error')
        raise
    finally:
        atomic(work/'status.json', state)
        if testfd is not None:
            os.close(testfd)
        if quarantinefd is not None:
            os.close(quarantinefd)
        os.close(rootfd); os.close(lock)
        print(json.dumps({'work': str(work), **state}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--role', required=True, choices=ROLES)
    parser.add_argument('--allow-own-test-cleanup', action='store_true')
    args = parser.parse_args()
    run(args.role, args.allow_own_test_cleanup)
