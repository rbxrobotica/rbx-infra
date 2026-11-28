#!/usr/bin/env python3
"""Mount one privately bound recovery unit read-only in the active GUI session.

UDisks denied mounting from the SSH session. This same bounded command may be
run by the owner in a native ThinkCentre terminal, without communicating with
another AI chat or changing authorization policies. No format/repair/raw IO.
"""
import argparse
import json
import os
from pathlib import Path
import stat
import subprocess

SIZES = {'A': 62914560000, 'B': 62930117632}


def locate(role):
    if os.uname().nodename != 'psyctl-ThinkCentre-E73':
        raise ValueError('unexpected_host')
    p = Path.home()/'.local/share/rbx-agent-handoffs/kitexec22/private-device-role-bindings.json'
    if p.resolve() != p or p.stat().st_uid != os.getuid() or p.stat().st_mode & 0o077:
        raise ValueError('unsafe_private_binding')
    binding = json.loads(p.read_text())['roles']['Kit '+role]
    disks = json.loads(subprocess.check_output(['lsblk', '-b', '-J', '-o',
        'NAME,PATH,TYPE,TRAN,RM,SIZE,SERIAL,FSTYPE,UUID,MOUNTPOINTS'], text=True))['blockdevices']
    found = [d for d in disks if d.get('type') == 'disk' and d.get('tran') == 'usb'
             and d.get('rm') and d.get('serial') == binding['serial'] and d.get('size') == SIZES[role]]
    if len(found) != 1:
        raise ValueError('expected_one_bound_USB')
    parts = found[0].get('children', [])
    if (len(parts) != 1 or parts[0].get('type') != 'part' or parts[0].get('fstype') != 'exfat'
            or parts[0].get('uuid') not in binding['filesystem_uuids']):
        raise ValueError('bound_filesystem_mismatch')
    part = parts[0]; source = Path(part['path'])
    if source.resolve() != source or not stat.S_ISBLK(source.stat().st_mode):
        raise ValueError('unexpected_block_source')
    mounts = [Path(m) for m in part.get('mountpoints', []) if m]
    if len(mounts) > 1:
        raise ValueError('multiple_mounts')
    if mounts and (mounts[0].resolve() != mounts[0] or not mounts[0].is_mount()
                   or mounts[0].stat().st_dev != source.stat().st_rdev):
        raise ValueError('mount_device_mismatch')
    return part, mounts


def main(role, check):
    part, mounts = locate(role)
    readonly = bool(mounts and os.statvfs(mounts[0]).f_flag & os.ST_RDONLY)
    if check:
        return {'role': 'Kit '+role, 'device_identity_checked': True,
                'mounted': bool(mounts), 'host_mount_readonly': readonly, 'mutation_attempted': False}
    if mounts and not readonly:
        r = subprocess.run(['udisksctl', 'unmount', '-b', part['path']], capture_output=True, timeout=60)
        if r.returncode:
            raise ValueError('normal_unmount_denied_or_busy')
        part, mounts = locate(role)
        if mounts:
            raise ValueError('mount_still_active')
    if not readonly:
        r = subprocess.run(['udisksctl', 'mount', '-b', part['path'], '-o', 'ro,nodev,nosuid,noexec'],
                           capture_output=True, timeout=60)
        if r.returncode:
            raise ValueError('read_only_mount_denied')
    _, mounts = locate(role)
    if len(mounts) != 1 or not os.statvfs(mounts[0]).f_flag & os.ST_RDONLY:
        raise ValueError('read_only_mount_not_verified')
    return {'role': 'Kit '+role, 'device_identity_checked': True,
            'host_mount_readonly': True, 'other_devices_or_files_changed': False}


if __name__ == '__main__':
    a = argparse.ArgumentParser(); a.add_argument('--role', choices=SIZES, required=True)
    a.add_argument('--check', action='store_true'); args = a.parse_args()
    try:
        print(json.dumps(main(args.role, args.check)))
    except Exception as error:
        print(json.dumps({'result': 'blocked', 'error_type': type(error).__name__,
                          'failure': str(error) if isinstance(error, ValueError) else 'system_operation_failed'}))
        raise SystemExit(1)
