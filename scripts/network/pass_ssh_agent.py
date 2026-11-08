#!/usr/bin/env python3
"""Activate one existing public identity in its existing local agent, on demand.

No SSH connections, agent creation, configuration writes, or private key files.
Output contains only fixed codes and coarse booleans, including on failure.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
from pathlib import Path
import pwd
import re
import selectors
import signal
import stat
import subprocess
import sys
import time

SSH = "/usr/bin/ssh"
KEYGEN = "/usr/bin/ssh-keygen"
ADD = "/usr/bin/ssh-add"
PASS = "/usr/bin/pass"
MAX_OUTPUT = 65536
MAX_PUBLIC = 16384
MAX_PRIVATE = 32768
FINGERPRINT = re.compile(rb"[1-9][0-9]{0,5} (SHA256:[A-Za-z0-9+/]{43})(?: [^\r\n]*)?")
ALGORITHMS = {
    b"ssh-ed25519", b"ssh-rsa", b"ecdsa-sha2-nistp256",
    b"ecdsa-sha2-nistp384", b"ecdsa-sha2-nistp521",
    b"sk-ssh-ed25519@openssh.com", b"sk-ecdsa-sha2-nistp256@openssh.com",
}


class SafeError(Exception):
    """Only constant, non-sensitive failure codes belong here."""


def run_command(argv, *, input_bytes=None, env=None, limit=MAX_OUTPUT, timeout=10):
    """Drain both pipes with a combined byte cap and a wall-clock deadline."""
    process = None
    selector = selectors.DefaultSelector()
    output = {"stdout": bytearray(), "stderr": bytearray()}
    offset = total = 0
    completed = False
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(
            argv, stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
            shell=False, start_new_session=True, bufsize=0,
        )
        for name in ("stdout", "stderr"):
            stream = getattr(process, name)
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        if input_bytes is not None:
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SafeError("command_timeout")
            for key, _ in selector.select(remaining):
                if key.data == "stdin":
                    try:
                        offset += os.write(key.fd, memoryview(input_bytes)[offset:])
                    except BrokenPipeError:
                        offset = len(input_bytes)
                    if offset == len(input_bytes):
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    continue
                chunk = os.read(key.fd, min(8192, limit + 1 - total))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                total += len(chunk)
                if total > limit:
                    raise SafeError("command_output_too_large")
                output[key.data].extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SafeError("command_timeout")
        try:
            code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise SafeError("command_timeout") from None
        completed = True
        return code, bytes(output["stdout"]), bytes(output["stderr"])
    except SafeError:
        raise
    except OSError:
        raise SafeError("command_unavailable") from None
    finally:
        selector.close()
        if process is not None:
            if not completed:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
                process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()


def absolute_path(value):
    if (not value or len(value) > 4096 or not value.startswith("/")
            or any(c.isspace() or ord(c) < 32 for c in value)
            or any(c in value for c in ("%", "$", "~", "\x7f"))):
        raise SafeError("unsafe_path")
    path = Path(value)
    if str(path) != value or any(part in (".", "..") for part in value.split("/")[1:]):
        raise SafeError("unsafe_path")
    return path


def open_parent(path):
    """Walk every directory using O_NOFOLLOW, returning the final parent FD."""
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:-1]:
            next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_fd
        return descriptor
    except OSError:
        os.close(descriptor)
        raise SafeError("unsafe_path") from None


def read_public(path, uid):
    parent = descriptor = None
    try:
        parent = open_parent(path)
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=parent)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid
                or info.st_mode & 0o022 or not 0 < info.st_size <= MAX_PUBLIC):
            raise SafeError("unsafe_public_key")
        value = os.read(descriptor, MAX_PUBLIC + 1)
        if len(value) > MAX_PUBLIC:
            raise SafeError("unsafe_public_key")
        parse_public(value)
        return value
    except OSError:
        raise SafeError("unsafe_public_key") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent is not None:
            os.close(parent)


def parse_public(value):
    lines = value.strip().splitlines()
    if len(lines) != 1:
        raise SafeError("invalid_public_key")
    fields = lines[0].split()
    if len(fields) < 2 or fields[0] not in ALGORITHMS:
        raise SafeError("invalid_public_key")
    try:
        blob = base64.b64decode(fields[1], validate=True)
    except (ValueError, binascii.Error):
        raise SafeError("invalid_public_key") from None
    if (len(blob) < 4 or int.from_bytes(blob[:4], "big") != len(fields[0])
            or blob[4:4 + len(fields[0])] != fields[0]):
        raise SafeError("invalid_public_key")
    return fields[0], fields[1]


def validate_socket(socket_path, runtime, uid):
    try:
        relative = socket_path.relative_to(runtime)
    except ValueError:
        raise SafeError("unsafe_agent_socket") from None
    if not relative.parts:
        raise SafeError("unsafe_agent_socket")
    parent = None
    try:
        # Opening the runtime itself also rejects symlinks in its ancestors.
        parent = open_parent(runtime / "unused")
        info = os.fstat(parent)
        if info.st_uid != uid or info.st_mode & 0o077:
            raise SafeError("unsafe_agent_socket")
        for part in relative.parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=parent)
            os.close(parent)
            parent = child
            info = os.fstat(parent)
            if info.st_uid != uid or info.st_mode & 0o077:
                raise SafeError("unsafe_agent_socket")
        info = os.stat(relative.name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077:
            raise SafeError("unsafe_agent_socket")
    except OSError:
        raise SafeError("agent_unavailable") from None
    finally:
        if parent is not None:
            os.close(parent)


def resolve(host, environment, runner):
    result, raw, _ = runner([SSH, "-G", "--", host], env=environment)
    if result:
        raise SafeError("ssh_config_unavailable")
    fields = {}
    known = {"host", "identityfile", "identityagent", "stricthostkeychecking",
             "forwardagent", "identitiesonly"}
    try:
        for line in raw.decode("utf-8", errors="strict").splitlines():
            name, separator, value = line.partition(" ")
            if not separator or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", name):
                raise SafeError("invalid_ssh_config")
            name = name.lower()
            if name in known:
                if name in fields or not value or len(value) > 4096:
                    raise SafeError("ambiguous_ssh_config")
                fields[name] = value
    except UnicodeError:
        raise SafeError("invalid_ssh_config") from None
    if set(fields) != known or fields["host"] != host:
        raise SafeError("incomplete_ssh_config")
    if (fields["stricthostkeychecking"] not in ("yes", "true")
            or fields["forwardagent"] != "no" or fields["identitiesonly"] != "yes"):
        raise SafeError("unsafe_ssh_policy")
    public_value = fields["identityfile"]
    if public_value.startswith("~/"):
        public_value = pwd.getpwuid(os.getuid()).pw_dir.rstrip("/") + public_value[1:]
    public = absolute_path(public_value)
    if public.suffix != ".pub":
        raise SafeError("public_identity_required")
    socket_path = absolute_path(fields["identityagent"])
    runtime = absolute_path(environment.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    raw_public = read_public(public, os.getuid())
    validate_socket(socket_path, runtime, os.getuid())
    return raw_public, socket_path, runtime


def fingerprint(raw, runner, environment):
    result, output, _ = runner(
        [KEYGEN, "-l", "-E", "sha256", "-f", "/dev/stdin"],
        input_bytes=raw, env=environment, limit=MAX_PUBLIC,
    )
    match = FINGERPRINT.fullmatch(output.strip())
    if result or match is None:
        raise SafeError("public_fingerprint_failed")
    return match.group(1)


def loaded(expected, runner, environment):
    result, output, error = runner([ADD, "-l", "-E", "sha256"], env=environment)
    if result == 1 and output.strip() == b"The agent has no identities." and not error:
        return False
    if result:
        raise SafeError("agent_unavailable")
    lines = output.strip().splitlines()
    if not 0 < len(lines) <= 256:
        raise SafeError("invalid_agent_response")
    matches = [FINGERPRINT.fullmatch(line) for line in lines]
    if any(match is None for match in matches):
        raise SafeError("invalid_agent_response")
    return any(match.group(1) == expected for match in matches)


def extract_private(raw):
    begin = b"-----BEGIN OPENSSH PRIVATE KEY-----"
    end = b"-----END OPENSSH PRIVATE KEY-----"
    if (len(raw) > MAX_OUTPUT or raw.count(b"-----BEGIN ") != 1
            or raw.count(b"-----END ") != 1):
        raise SafeError("invalid_private_key_block")
    match = re.search(rb"(?m)^" + begin + rb"\r?\n([A-Za-z0-9+/=\r\n]+)\r?\n"
                      + end + rb"\r?$", raw)
    if match is None or len(match.group(0)) > MAX_PRIVATE:
        raise SafeError("invalid_private_key_block")
    try:
        decoded = base64.b64decode(b"".join(match.group(1).splitlines()), validate=True)
    except (ValueError, binascii.Error):
        raise SafeError("invalid_private_key_block") from None
    if not decoded.startswith(b"openssh-key-v1\x00"):
        raise SafeError("invalid_private_key_block")
    return match.group(0).replace(b"\r\n", b"\n") + b"\n"


def valid_entry(value):
    return (isinstance(value, str) and len(value) <= 256
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+@/-]*", value)
            and all(part not in ("", ".", "..") and not part.startswith("-")
                    for part in value.split("/")))


def activate(action, host, pass_entry=None, ttl=900, *, runner=None, environment=None):
    report = {"code": "operation_failed", "configuration_valid": False,
              "agent_available": False, "expected_key_loaded": False, "changed": False}
    try:
        if (action not in ("status", "load") or not isinstance(host, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", host) is None
                or type(ttl) is not int or not 60 <= ttl <= 3600
                or (action == "load" and not valid_entry(pass_entry))
                or (action == "status" and pass_entry is not None)):
            raise SafeError("invalid_arguments")
        runner = runner or run_command
        environment = dict(os.environ if environment is None else environment)
        environment.update(LC_ALL="C", SSH_ASKPASS_REQUIRE="never")
        environment.pop("SSH_ASKPASS", None)
        raw_public, socket_path, runtime = resolve(host, environment, runner)
        report["configuration_valid"] = True
        environment["SSH_AUTH_SOCK"] = str(socket_path)
        expected = fingerprint(raw_public, runner, environment)
        present = loaded(expected, runner, environment)
        report.update(agent_available=True, expected_key_loaded=present)
        if present or action == "status":
            report["code"] = "ready" if present else "key_missing"
            return report
        result, output, _ = runner([PASS, "show", "--", pass_entry], env=environment,
                                   timeout=30, limit=MAX_OUTPUT)
        if result:
            raise SafeError("pass_read_failed")
        private = extract_private(output)
        result, derived, _ = runner(
            [KEYGEN, "-y", "-P", "", "-f", "/dev/stdin"], input_bytes=private,
            env=environment, limit=MAX_PUBLIC,
        )
        if result:
            raise SafeError("private_key_derivation_failed")
        if parse_public(derived) != parse_public(raw_public):
            raise SafeError("public_key_mismatch")
        validate_socket(socket_path, runtime, os.getuid())
        result, _, _ = runner([ADD, "-t", str(ttl), "-"], input_bytes=private,
                              env=environment)
        if result:
            raise SafeError("agent_load_failed")
        report["changed"] = True
        if not loaded(expected, runner, environment):
            raise SafeError("agent_load_unverified")
        report.update(code="loaded", expected_key_loaded=True)
    except SafeError as error:
        report["code"] = error.args[0]
    except Exception:
        report["code"] = "operation_failed"
    return report


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise SafeError("invalid_arguments")


def main(argv=None):
    try:
        parser = SafeParser(description=__doc__)
        parser.add_argument("action", choices=("status", "load"))
        parser.add_argument("--host", required=True)
        parser.add_argument("--pass-entry")
        parser.add_argument("--ttl", default="900")
        args = parser.parse_args(argv)
        if re.fullmatch(r"[0-9]{2,4}", args.ttl) is None:
            raise SafeError("invalid_arguments")
        report = activate(args.action, args.host, args.pass_entry, int(args.ttl))
    except SafeError:
        report = {"code": "invalid_arguments", "configuration_valid": False,
                  "agent_available": False, "expected_key_loaded": False, "changed": False}
    print(json.dumps(report, sort_keys=True))
    return 0 if report["code"] in ("ready", "loaded") else 1


if __name__ == "__main__":
    sys.exit(main())
