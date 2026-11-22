#!/usr/bin/env python3
"""A narrowly authorized recovery IRC responder, never an IRC task executor.

Pure decisions: pinned local context hashes, PING and curated STATUS only.
The daemon never starts an agent, SSH, shell, GPG, media or backup operation.
Explicitly started recovery jobs remain a separate governed execution path.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import subprocess
import time


def read_private(root, relative, limit):
    root = Path(root)
    parts = PurePosixPath(relative).parts
    if (not isinstance(relative, str) or not parts or relative != '/'.join(parts)
            or any(p in ('', '.', '..') for p in parts)
            or PurePosixPath(relative).is_absolute() or root.resolve() != root):
        raise ValueError('unsafe_local_reference')
    fd = os.open(root, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        s = os.fstat(fd)
        if s.st_uid != os.getuid() or s.st_mode & 0o077:
            raise ValueError('unsafe_context_root')
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = child
            s = os.fstat(fd)
            if s.st_uid != os.getuid() or s.st_mode & 0o077:
                raise ValueError('unsafe_context_parent')
        child = os.open(parts[-1], os.O_RDONLY|os.O_NOFOLLOW, dir_fd=fd)
        try:
            s = os.fstat(child)
            if (not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid()
                    or s.st_mode & 0o077 or not 0 < s.st_size <= limit):
                raise ValueError('unsafe_context_file')
            raw = os.read(child, limit+1)
            after = os.fstat(child)
            if len(raw) != s.st_size or (after.st_size, after.st_mtime_ns) != (s.st_size, s.st_mtime_ns):
                raise ValueError('context_changed_during_read')
            return raw
        finally:
            os.close(child)
    finally:
        os.close(fd)


def respond(row, registry, root, now):
    try:
        if (registry.get('task') != 'recovery-kits'
                or type(registry.get('expires_epoch')) is not int
                or registry['expires_epoch'] <= now
                or row.get('direction') != 'in' or row.get('status') != 'pending'
                or row.get('peer') != registry.get('allowed_peer', 'local')
                or row.get('account') != registry.get('allowed_account', 'rbx-local')
                or not isinstance(row.get('id'), str)
                or not re.fullmatch('[0-9a-f]{32}', row['id'])
                or not isinstance(row.get('text'), str)
                or len(row['text'].encode()) > 240):
            return None
        message = row['text']
        if message == 'PING task=recovery-kits':
            return 'PONG task=recovery-kits'
        if message == 'STATUS task=recovery-kits':
            try:
                status_data = json.loads(read_private(root, registry['status_relative_path'], 4096))
                phases = {'ready', 'idle', 'prepared', 'capacity_write', 'capacity_read',
                          'awaiting_restore', 'awaiting_ciphertext', 'waiting_outer_unlock',
                          'waiting_inner_unlock', 'restoring', 'verifying', 'finished', 'blocked'}
                results = {'ok', 'running', 'pending', 'passed', 'failed', 'blocked', 'not_started'}
                if (not isinstance(status_data, dict) or status_data.get('phase') not in phases
                        or status_data.get('result') not in results
                        or type(status_data.get('count')) is not int
                        or not 0 <= status_data['count'] <= 100000):
                    raise ValueError('unsafe_status')
                return ('STATUS task=recovery-kits phase='+status_data['phase']+
                        ' count='+str(status_data['count'])+' result='+status_data['result'])
            except (OSError, ValueError, KeyError, TypeError):
                return 'STATUS task=recovery-kits phase=blocked count=0 result=unavailable'
        match = re.fullmatch(r'CTX task=([A-Za-z0-9_-]{1,32}) path=(\S+) sha256=(\S+) '
                             r'src=(\S+) reply=CONTEXT_OK', message)
        if not match:
            return None
        task, path, digest, source = match.groups()
        approved = registry.get('contexts', {}).get(task)
        if approved is None:
            return None
        reject = 'CONTEXT_REJECT task='+task
        try:
            relative = approved['relative_path']
            if (source != registry.get('allowed_source_alias', 'rbx-local')
                    or not isinstance(relative, str) or not relative.endswith('.md')
                    or path != '~/.local/share/rbx-agent-handoffs/'+relative
                    or not re.fullmatch('[0-9a-f]{64}', digest)
                    or digest != approved['sha256']):
                return reject
            raw = read_private(root, relative, 32768)
            if hashlib.sha256(raw).hexdigest() != digest:
                return reject
            return 'CONTEXT_OK task='+task+' sha256='+digest
        except (OSError, ValueError, KeyError, TypeError):
            return reject
    except (OSError, ValueError, KeyError, TypeError, UnicodeError):
        return None


def once(registry_path, root, mailbox, cli, config):
    raw = read_private(registry_path.parent, registry_path.name, 16384)
    registry = json.loads(raw)
    # The existing reply CLI/package is pinned by the explicit installer, not
    # selected or changed through incoming messages.
    for name, expected in registry['dependency_pins'].items():
        p = Path(name)
        if p.resolve() != p or hashlib.sha256(p.read_bytes()).hexdigest() != expected:
            raise ValueError('mailbox_dependency_drift')
    info = mailbox.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or mailbox.resolve() != mailbox):
        raise ValueError('unsafe_mailbox')
    with sqlite3.connect('file:'+str(mailbox)+'?mode=ro', uri=True, timeout=2) as db:
        db.execute('PRAGMA query_only=ON')
        db.row_factory = sqlite3.Row
        rows = [dict(r) for r in db.execute(
            "SELECT * FROM requests WHERE direction='in' AND status='pending' "
            'AND peer=? AND account=? ORDER BY timestamp,id LIMIT 100',
            (registry.get('allowed_peer', 'local'), registry.get('allowed_account', 'rbx-local')))]
    queued = 0
    for row in rows:
        answer = respond(row, registry, root, int(time.time()))
        if answer is None:
            continue
        # A fixed local mailbox reply only. There is no shell or code handler.
        r = subprocess.run([str(cli), '--config', str(config), 'reply', row['id'], answer],
                           capture_output=True, timeout=10)
        queued += r.returncode == 0
    print(json.dumps({'scanned_addressed_rows': len(rows), 'replies_queued': queued,
                      'executor_or_secret_access': False}))


if __name__ == '__main__':
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('--registry', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--mailbox', type=Path, required=True)
    parser.add_argument('--cli', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    try:
        once(args.registry, args.root, args.mailbox, args.cli, args.config)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps({'result': 'blocked', 'error_type': type(error).__name__}))
        raise SystemExit(1)
