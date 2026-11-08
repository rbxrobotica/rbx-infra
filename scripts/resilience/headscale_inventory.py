#!/usr/bin/env python3
"""Bounded, self-contained, read-only Headscale metadata observation."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import PurePosixPath
import re
import selectors
import shlex
import signal
import stat
import subprocess
import sys
import time

CONFIG = '/etc/headscale/config.yaml'
ROOTS = ('/var/lib/headscale', '/var/backups/headscale')
SCRIPTS = ('/usr/local/sbin/headscale-backup', '/usr/local/sbin/headscale-restore-test',
           '/usr/local/sbin/rbx-headscale-backup', '/usr/local/sbin/rbx-headscale-restore-test')
KNOWN_POLICY = '/etc/headscale/policy.hujson'
MAX_CONFIG, MAX_NODES, MAX_DEPTH = 1048576, 20000, 64
MAX_ENTRIES, TREE_DEPTH = 256, 3
REFS = ('database.sqlite.path', 'noise.private_key_path', 'policy.path',
        'derp.server.private_key_path', 'tls_key_path', 'tls_cert_path',
        'tls_letsencrypt_cache_dir', 'oidc.client_secret_path')
UNITS = ('headscale.service', 'headscale-backup.service', 'headscale-backup.timer',
         'rbx-headscale-backup.service', 'rbx-headscale-backup.timer')
REF_PROPERTIES = ('FragmentPath', 'DropInPaths', 'EnvironmentFiles')
PROPERTIES = ('ActiveState', 'SubState', 'Result', 'ExecMainStatus', 'UnitFileState',
              'LastTriggerUSec', 'NextElapseUSecRealtime', *REF_PROPERTIES)
KNOWN_PATHS = frozenset((CONFIG, KNOWN_POLICY, *ROOTS, *SCRIPTS,
    '/var/lib/headscale/db.sqlite', '/var/lib/headscale/db.sqlite3',
    '/var/lib/headscale/headscale.db', '/var/lib/headscale/noise_private.key',
    '/var/lib/headscale/derp_server_private.key', '/etc/headscale/policy.json',
    '/etc/headscale/acl.hujson', '/etc/default/headscale',
    *('/etc/systemd/system/' + unit for unit in UNITS),
    *('/lib/systemd/system/' + unit for unit in UNITS),
    *('/usr/lib/systemd/system/' + unit for unit in UNITS),
    *('/etc/systemd/system/' + unit + '.d/override.conf' for unit in UNITS)))
STATES = frozenset(('active','reloading','inactive','failed','activating','deactivating',
    'maintenance','refreshing','running','dead','exited','waiting','elapsed','start',
    'start-pre','start-post','stop','stop-sigterm','stop-sigkill','stop-post','final-sigterm',
    'final-sigkill','auto-restart','auto-restart-queued','condition','cleaning','success',
    'resources','timeout','exit-code','signal','core-dump','watchdog','start-limit-hit',
    'protocol','oom-kill','exec-condition','assert','dependency','enabled','enabled-runtime',
    'linked','linked-runtime','alias','masked','masked-runtime','static','disabled',
    'indirect','generated','transient','bad','not-found',''))
try:
    import yaml
except ImportError:
    yaml = None

class Stop(Exception):
    pass

class Deadline(BaseException):
    pass

def require(value, code):
    if not value:
        raise Stop(code)

if yaml is not None:
    class BoundedSafeLoader(yaml.SafeLoader):
        def __init__(self, stream):
            super().__init__(stream)
            self.node_count = self.depth = 0
        def compose_node(self, parent, index):
            self.node_count += 1
            self.depth += 1
            try:
                require(self.node_count <= MAX_NODES and self.depth <= MAX_DEPTH
                        and not self.check_event(yaml.AliasEvent), 'unsupported_yaml_structure')
                return super().compose_node(parent, index)
            finally:
                self.depth -= 1
        def construct_mapping(self, node, deep=False):
            result = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                require(type(key) is str and key not in result, 'ambiguous_yaml_mapping')
                result[key] = self.construct_object(value_node, deep=deep)
            return result

def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

def components(path):
    require(type(path) is str and path.startswith('/') and len(path) <= 4096
            and '\x00' not in path and '..' not in path.split('/'), 'invalid_path_reference')
    parts = PurePosixPath(path).parts[1:]
    require(bool(parts), 'invalid_path_reference')
    return parts

def parent_fd(path):
    parts = components(path)
    directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            info = os.lstat(part, dir_fd=directory)
            require(stat.S_ISDIR(info.st_mode), 'non_directory_or_symlink_component')
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            if (os.fstat(next_fd).st_dev, os.fstat(next_fd).st_ino) != (info.st_dev, info.st_ino):
                os.close(next_fd)
                raise Stop('path_changed')
            os.close(directory)
            directory = next_fd
        return directory, parts[-1]
    except Exception:
        os.close(directory)
        raise

def read_config():
    directory = descriptor = None
    try:
        directory, name = parent_fd(CONFIG)
        before = os.lstat(name, dir_fd=directory)
        require(stat.S_ISREG(before.st_mode) and before.st_size <= MAX_CONFIG, 'config_not_bounded_regular')
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        opened = os.fstat(descriptor)
        require(fingerprint(opened) == fingerprint(before), 'config_changed')
        chunks, size = [], 0
        while size <= MAX_CONFIG:
            chunk = os.read(descriptor, min(65536, MAX_CONFIG + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        require(size <= MAX_CONFIG, 'config_too_large')
        require(fingerprint(os.fstat(descriptor)) == fingerprint(opened)
                and fingerprint(os.lstat(name, dir_fd=directory)) == fingerprint(opened), 'config_changed')
        return b''.join(chunks)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)

def parse_config(raw):
    require(yaml is not None, 'pyyaml_unavailable')
    try:
        value = yaml.load(raw.decode('utf-8', errors='strict'), Loader=BoundedSafeLoader)
        require(type(value) is dict, 'config_root_not_mapping')
        return value
    except Stop:
        raise
    except Exception:
        raise Stop('config_unparseable') from None

def field(config, dotted):
    value = config
    for part in dotted.split('.'):
        if type(value) is not dict or part not in value:
            return None
        value = value[part]
    return value

def path_label(path):
    return {'path_sha256': hashlib.sha256(path.encode('utf-8', errors='surrogatepass')).hexdigest(),
            'known_path': path if path in KNOWN_PATHS else None,
            'root_class': next((root for root in ('/etc/headscale', *ROOTS)
                               if path == root or path.startswith(root + '/')), 'other_absolute')}

def stat_fields(info):
    kind = ('regular' if stat.S_ISREG(info.st_mode) else 'directory' if stat.S_ISDIR(info.st_mode)
            else 'symlink' if stat.S_ISLNK(info.st_mode) else 'other')
    return {'kind': kind, 'mode': format(stat.S_IMODE(info.st_mode), '04o'),
            'uid': info.st_uid, 'gid': info.st_gid, 'size_bytes': info.st_size, 'mtime_ns': info.st_mtime_ns}

def metadata(path):
    directory = None
    try:
        directory, name = parent_fd(path)
        return {'status': 'observed', **stat_fields(os.lstat(name, dir_fd=directory))}
    except FileNotFoundError:
        return {'status': 'absent'}
    except PermissionError:
        return {'status': 'permission_denied'}
    except Stop as exc:
        return {'status': exc.args[0]}
    except OSError:
        return {'status': 'metadata_unavailable'}
    finally:
        if directory is not None:
            os.close(directory)

def reference(value):
    item = {'present': value is not None, 'nonempty_string': type(value) is str and bool(value)}
    if item['nonempty_string']:
        try:
            components(value)
            item.update(path_label(value), metadata=metadata(value))
        except Stop:
            item['status'] = 'invalid_or_relative_path_redacted'
    return item

def config_summary(config):
    mode = field(config, 'policy.mode')
    flags = {'policy_mode': mode if type(mode) is str and mode in ('file', 'database') else None,
             'policy_mode_present': mode is not None,
             'derp_embedded_enabled': field(config, 'derp.server.enabled')
                 if type(field(config, 'derp.server.enabled')) is bool else None,
             'oidc_section_present': type(field(config, 'oidc')) is dict,
             'oidc_inline_secret_present': field(config, 'oidc.client_secret') not in (None, ''),
             'oidc_secret_path_present': field(config, 'oidc.client_secret_path') not in (None, '')}
    return {'references': {name: reference(field(config, name)) for name in REFS}, 'flags': flags}

def tree_inventory(roots=ROOTS):
    output = {'entry_limit': MAX_ENTRIES, 'depth_limit': TREE_DEPTH, 'entries': [], 'roots': [],
              'truncated': False, 'depth_limited': False, 'entries_attempted': 0, 'errors': []}
    def visit(fd, parent_path, depth):
        with os.scandir(fd) as entries:
            for entry in entries:
                if output['entries_attempted'] >= MAX_ENTRIES:
                    output['truncated'] = True
                    return
                output['entries_attempted'] += 1
                path = parent_path + '/' + entry.name
                try:
                    info = os.lstat(entry.name, dir_fd=fd)
                    output['entries'].append({'alias': 'entry_' + str(len(output['entries']) + 1).zfill(3),
                        'depth': depth, **path_label(path), **stat_fields(info)})
                    if stat.S_ISDIR(info.st_mode):
                        if depth >= TREE_DEPTH:
                            output['depth_limited'] = True
                        else:
                            child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                            try:
                                require((os.fstat(child).st_dev, os.fstat(child).st_ino)
                                        == (info.st_dev, info.st_ino), 'path_changed')
                                visit(child, path, depth + 1)
                            finally:
                                os.close(child)
                except (OSError, Stop):
                    if 'entry_metadata_or_directory_unavailable' not in output['errors']:
                        output['errors'].append('entry_metadata_or_directory_unavailable')
    for root in roots:
        output['roots'].append({**path_label(root), 'metadata': metadata(root)})
        directory = child = None
        try:
            directory, name = parent_fd(root)
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            visit(child, root, 1)
        except (OSError, Stop):
            output['errors'].append('root_unavailable')
        finally:
            if child is not None:
                os.close(child)
            if directory is not None:
                os.close(directory)
    return output

def query(argv):
    """Fixed read-only argv only; output is bounded while read, never logged raw."""
    process = None
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'},
            cwd='/', close_fds=True, shell=False)
        deadline, data = time.monotonic() + 4, bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                require(remaining > 0, 'query_timeout')
                if not selector.select(remaining):
                    raise Stop('query_timeout')
                chunk = os.read(process.stdout.fileno(), 4096)
                if not chunk:
                    break
                data.extend(chunk)
                require(len(data) <= 32768, 'query_output_too_large')
            require(process.wait(timeout=max(0.01, deadline - time.monotonic())) == 0, 'query_nonzero')
        return bytes(data).decode('utf-8', errors='strict')
    except Stop:
        raise
    except Exception:
        raise Stop('query_unavailable') from None
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=1)
            if process.stdout is not None:
                process.stdout.close()

def unit_references(key, value):
    if key == 'FragmentPath':
        paths = [] if not value else [value]
    elif key == 'DropInPaths':
        try:
            paths = shlex.split(value)
        except ValueError:
            raise Stop('unit_reference_invalid') from None
    else:
        paths = re.findall(r'(/[^ \t\n]+) \(ignore_errors=(?:yes|no)\)', value)
        remainder = re.sub(r'(/[^ \t\n]+) \(ignore_errors=(?:yes|no)\)', '', value).strip()
        require(not remainder, 'unit_reference_invalid')
    require(len(paths) <= 16 and all(len(path) <= 4096 for path in paths), 'unit_reference_limit')
    return [reference(path) for path in paths]

def unit_summary(unit):
    try:
        raw = query(['/usr/bin/systemctl', 'show', '--no-pager',
                     *['--property=' + name for name in PROPERTIES], unit])
        values, unknown = {}, False
        for line in raw.splitlines():
            key, separator, value = line.partition('=')
            require(separator and key in PROPERTIES and key not in values, 'systemctl_response_invalid')
            if key in REF_PROPERTIES:
                values[key] = unit_references(key, value)
            elif key == 'ExecMainStatus':
                values[key] = int(value) if re.fullmatch(r'[0-9]{1,3}', value) else None
            elif key in ('LastTriggerUSec', 'NextElapseUSecRealtime'):
                values[key] = value if value in ('', 'n/a') or re.fullmatch(
                    r'(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) [0-9]{4}-[0-9]{2}-[0-9]{2} '
                    r'[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})? (?:UTC|GMT|[+-][0-9]{2}(?::?[0-9]{2})?)', value) else None
            else:
                values[key] = value if value in STATES else None
            unknown |= values[key] is None
        require({'ActiveState', 'SubState', 'UnitFileState'} <= set(values), 'systemctl_response_incomplete')
        return {'status': 'observed', 'properties': values, 'unrecognized_value_redacted': unknown}
    except Stop as exc:
        return {'status': exc.args[0]}

def versions():
    output = {'python': '.'.join(map(str, sys.version_info[:3])), 'pyyaml_available': yaml is not None}
    for package in ('headscale', 'sqlite3', 'gpg'):
        try:
            # Query package metadata only; never execute headscale or gpg.
            raw = query(['/usr/bin/dpkg-query', '-W', '-f=${Version}\n', package])
            match = re.fullmatch(r'([0-9][0-9A-Za-z.+:~\-]{0,100})\n?', raw)
            output[package + '_package'] = {'status': 'observed', 'version': match.group(1)} if match else {'status': 'version_redacted'}
        except Stop as exc:
            output[package + '_package'] = {'status': exc.args[0]}
    return output

def observe():
    report = {'schema': 'rbx.headscale.readonly-inventory.v1', 'config': {},
              'database_opened': False, 'referenced_secret_or_archive_files_opened': False,
              'headscale_invoked': False, 'writes_requested': False}
    try:
        raw = read_config()
        report['config'] = {'read': True, 'size_bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        report['config'].update(config_summary(parse_config(raw)), parsed=True)
    except Stop as exc:
        report['config'].update(parsed=False, error=exc.args[0])
    except Exception:
        report['config'].update(parsed=False, error='config_unavailable')
    report['filesystem'] = tree_inventory()
    report['script_metadata'] = {path: metadata(path) for path in SCRIPTS}
    report['known_policy_metadata'] = {KNOWN_POLICY: metadata(KNOWN_POLICY)}
    report['units'] = {unit: unit_summary(unit) for unit in UNITS}
    report['versions'] = versions()
    report['observation_only_no_backup_or_recovery_proven'] = True
    return report

def main():
    def expired(signum, frame):
        raise Deadline()
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(38)
    try:
        print(json.dumps(observe(), sort_keys=True, separators=(',', ':')))
        return 0
    except (Exception, Deadline):
        print(json.dumps({'schema': 'rbx.headscale.readonly-inventory.v1',
                          'error': 'observation_incomplete_redacted'}))
        return 1
    finally:
        signal.alarm(0)

if __name__ == '__main__':
    sys.exit(main())
