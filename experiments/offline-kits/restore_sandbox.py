#!/usr/bin/env python3
"""Launch a reviewed capsule restore; only native on-host prompts accept factors.

This is an explicit SSH-started job, never an IRC command handler. It exposes
only the closed capsule and reviewed program to the networkless worker, while
masking the operational home, run directory and GPG sockets. The desktop bus and
X socket are exposed solely for native pinentry. No secret enters argv or logs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid


def main(program, expected_program, ciphertext, expected_cipher, check):
    if os.uname().nodename != 'psyctl-ThinkCentre-E73':
        raise ValueError('unexpected_host')
    for path, expected in ((program, expected_program), (ciphertext, expected_cipher)):
        if (path.resolve() != path or not re.fullmatch('[0-9a-f]{64}', expected)
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            raise ValueError('reviewed_input_mismatch')
    lines = subprocess.check_output(['systemctl', '--user', 'show-environment'], text=True).splitlines()
    env = dict(x.split('=', 1) for x in lines if '=' in x)
    if any(not env.get(k) for k in ('DISPLAY', 'XAUTHORITY', 'DBUS_SESSION_BUS_ADDRESS')):
        raise ValueError('native_desktop_unavailable')
    auth = Path(env['XAUTHORITY'])
    bus = Path('/run/user')/str(os.getuid())/'bus'
    if not auth.is_file() or not bus.is_socket():
        raise ValueError('native_desktop_socket_unavailable')
    uid = str(os.getuid()); home = str(Path.home())
    args = ['systemd-run', '--user', '--pipe', '--wait', '--collect',
        '--unit=rbx-kit-restore-'+uuid.uuid4().hex[:12],
        '--property=MemorySwapMax=0', '--property=MemoryMax=512M', '--property=LimitCORE=0',
        '--property=RuntimeMaxSec=2400', '/usr/bin/bwrap', '--die-with-parent', '--new-session',
        '--unshare-user', '--unshare-pid', '--unshare-net', '--unshare-ipc', '--unshare-uts',
        '--ro-bind', '/', '/', '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
        '--tmpfs', '/dev/shm', '--tmpfs', home, '--tmpfs', '/run',
        '--dir', '/run/user/'+uid, '--ro-bind', str(bus), str(bus),
        '--ro-bind', '/tmp/.X11-unix', '/tmp/.X11-unix',
        '--dir', '/tmp/work', '--ro-bind', str(auth), '/tmp/work/Xauthority',
        '--ro-bind', str(program), '/tmp/work/kit_package.py',
        '--ro-bind', str(ciphertext), '/tmp/work/recovery-kit.tar.gpg',
        '--dir', home+'/.gnupg', '--dir', home+'/.password-store',
        '--setenv', 'HOME', home, '--setenv', 'DISPLAY', env['DISPLAY'],
        '--setenv', 'XAUTHORITY', '/tmp/work/Xauthority',
        '--setenv', 'DBUS_SESSION_BUS_ADDRESS', env['DBUS_SESSION_BUS_ADDRESS'],
        '--unsetenv', 'GNUPGHOME', '--unsetenv', 'GPG_AGENT_INFO',
        '--chdir', '/tmp/work', '/usr/bin/python3', '-I', '-B']
    if check:
        args += ['-c', 'import sys;sys.path.insert(0,"/tmp/work");import kit_package as k;'
            'from pathlib import Path;import json;k.memory_gate();'
            'assert {x.split(":")[0].strip() for x in Path("/proc/net/dev").read_text().splitlines()[2:]}=={"lo"};'
            'assert all(not any(p.iterdir()) for p in (Path.home()/".gnupg",Path.home()/".password-store"));'
            'print(json.dumps({"sandbox_preflight":"passed","network_disabled":True,"operational_home_masked":True,"no_swap_cgroup":True,"decrypt_attempted":False}))']
    else:
        args += ['/tmp/work/kit_package.py', 'restore', '--ciphertext', '/tmp/work/recovery-kit.tar.gpg',
                 '--sha256', expected_cipher]
    result = subprocess.run(args)
    if result.returncode:
        raise ValueError('sandbox_job_failed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--program', type=Path, required=True)
    parser.add_argument('--program-sha256', required=True)
    parser.add_argument('--ciphertext', type=Path, required=True)
    parser.add_argument('--ciphertext-sha256', required=True)
    parser.add_argument('--check', action='store_true')
    a = parser.parse_args()
    try:
        main(a.program, a.program_sha256, a.ciphertext, a.ciphertext_sha256, a.check)
    except Exception as e:
        print(json.dumps({'result': 'failed', 'error_type': type(e).__name__,
                          'failure': str(e) if isinstance(e, ValueError) else 'system_operation_failed'}))
        raise SystemExit(1)
