#!/usr/bin/env python3
"""Initial user-service installation only, from a reviewed public stage.

Run on ThinkCentre with its existing mailbox venv. Never overwrites an existing
unit/registry or changes/restarts the IRC transport. No password is copied.
"""
import argparse
import datetime
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def write_new(path, raw, mode=0o600):
    fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def main(stage, expected):
    import strategos_irc_bot.agent_cli as agent_cli
    import strategos_irc_bot.agent_store as agent_store
    os.umask(0o077)
    if os.uname().nodename != 'psyctl-ThinkCentre-E73':
        raise ValueError('unexpected_host')
    if stage.resolve() != stage or hashlib.sha256((stage/'manifest.json').read_bytes()).hexdigest() != expected:
        raise ValueError('untrusted_stage')
    manifest = json.loads((stage/'manifest.json').read_text())
    names = {'responder.py', 'recovery-irc.service.in', 'recovery-irc.timer'}
    if set(manifest) != names:
        raise ValueError('unexpected_stage_members')
    for name, digest in manifest.items():
        p = stage/name
        if p.resolve() != p or hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            raise ValueError('stage_content_drift')
    home = Path.home()
    units = home/'.config/systemd/user'
    data = home/'.local/share/rbx-recovery-irc'
    release = data/'releases'/expected
    registry_path = data/'registry.json'
    if (any((units/n).exists() for n in ('rbx-recovery-irc.service', 'rbx-recovery-irc.timer'))
            or registry_path.exists()):
        raise ValueError('existing_responder_requires_reviewed_update')
    for p in (units, data, release):
        p.mkdir(mode=0o700, parents=True, exist_ok=True)
        if p.resolve() != p or p.stat().st_uid != os.getuid():
            raise ValueError('unsafe_install_directory')
    root = home/'.local/share/rbx-agent-handoffs'
    context_dir = root/'ircboot22'
    context_dir.mkdir(mode=0o700)
    context = b'# Recovery kits communication canary\nApproved metadata only; no execution capability.\n'
    write_new(context_dir/'CONTEXT.md', context)
    status = root/'recovery-kits-status.json'
    write_new(status, b'{"phase":"awaiting_restore","count":0,"result":"pending"}\n')
    dependency_paths = [home/'.local/share/rbx-agent-irc/venv/bin/rbx-agent-irc',
                        Path(inspect.getsourcefile(agent_cli)), Path(inspect.getsourcefile(agent_store))]
    registry = {'task': 'recovery-kits', 'expires_epoch': int(time.time())+60*86400,
        'allowed_peer': 'local', 'allowed_account': 'rbx-local', 'allowed_source_alias': 'rbx-local',
        'status_relative_path': 'recovery-kits-status.json',
        'contexts': {'ircboot22': {'relative_path': 'ircboot22/CONTEXT.md',
                                  'sha256': hashlib.sha256(context).hexdigest()}},
        'dependency_pins': {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in dependency_paths}}
    write_new(registry_path, (json.dumps(registry, indent=2)+'\n').encode())
    write_new(release/'responder.py', (stage/'responder.py').read_bytes(), 0o500)
    unit = (stage/'recovery-irc.service.in').read_text().replace('@RELEASE@', str(release))
    write_new(units/'rbx-recovery-irc.service', unit.encode())
    write_new(units/'rbx-recovery-irc.timer', (stage/'recovery-irc.timer').read_bytes())
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'start', 'rbx-recovery-irc.service'], check=True)
    result = subprocess.check_output(['systemctl', '--user', 'show',
                 'rbx-recovery-irc.service', '-p', 'Result', '--value'], text=True).strip()
    if result != 'success':
        raise ValueError('first_scoped_responder_tick_failed')
    subprocess.run(['systemctl', '--user', 'enable', '--now', 'rbx-recovery-irc.timer'], check=True)
    receipt = {'installed_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'initial_tick': result, 'timer_active': subprocess.run(['systemctl', '--user', 'is-active',
          '--quiet', 'rbx-recovery-irc.timer']).returncode == 0,
        'context_task': 'ircboot22', 'context_sha256': hashlib.sha256(context).hexdigest(),
        'registry_expires_epoch': registry['expires_epoch'], 'stage_manifest_sha256': expected,
        'scope': 'pinned context ACK, PING and curated STATUS; no task/secret execution',
        'existing_transport_restarted_or_configured': False}
    write_new(data/'installation.json', (json.dumps(receipt, indent=2)+'\n').encode())
    print(json.dumps(receipt))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True)
    args = parser.parse_args()
    try:
        main(args.stage, args.manifest_sha256)
    except Exception as error:
        print(json.dumps({'installed': False, 'error_type': type(error).__name__,
                          'failure': str(error) if isinstance(error, ValueError) else 'system_error'}))
        raise SystemExit(1)
