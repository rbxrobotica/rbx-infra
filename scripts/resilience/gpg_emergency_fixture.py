#!/usr/bin/env python3
"""Generated GPG export/wrap/import compatibility fixture; never a real-key CLI."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time

try:
    from . import local_fixture as fixture
except ImportError:
    import local_fixture as fixture

SCHEMA = "rbx.resilience.gpg-emergency-fixture.v1"
MAX_MEMBER_BYTES = 256 * 1024
MAX_KIT_BYTES = 2 * 1024 * 1024
MAX_DIAGNOSTICS = 64 * 1024
S2K_COUNT = 65011712
FILES = frozenset({"private-key.gpg", "public-key.gpg", "credential.gpg",
                   "revocation.txt", "recovery.json", "instructions.txt"})
CANARY = b"RBX synthetic emergency-key recovery canary v1\n"
ENVIRONMENT = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
INSTRUCTIONS = b"Synthetic only: unwrap independently; import into a fresh keyring; verify the canary.\n"


class KitError(Exception):
    """Only fixed failure codes may leave the fixture."""


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def strict_json(value):
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise KitError("duplicate_json_key")
            result[key] = item
        return result
    def constant(_):
        raise KitError("invalid_json_number")
    return json.loads(value, object_pairs_hook=pairs, parse_constant=constant)


def run_command(argv, *, input_bytes=None, limit=MAX_KIT_BYTES + MAX_DIAGNOSTICS, timeout=30):
    """Cap both pipes and elapsed time; never inherit the operator environment."""
    process = None
    selector = selectors.DefaultSelector()
    streams = {"stdout": bytearray(), "stderr": bytearray()}
    total = offset = 0
    done = False
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                                   start_new_session=True, bufsize=0, env=ENVIRONMENT.copy())
        for name in streams:
            stream = getattr(process, name)
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        if input_bytes is not None:
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise KitError("command_timeout")
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
                chunk = os.read(key.fd, min(65536, limit + 1 - total))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                total += len(chunk)
                if total > limit:
                    raise KitError("command_output_bound")
                streams[key.data].extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise KitError("command_timeout")
        try:
            result = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise KitError("command_timeout") from None
        done = True
        return result, bytes(streams["stdout"]), bytes(streams["stderr"])
    except KitError:
        raise
    except OSError:
        raise KitError("command_unavailable") from None
    finally:
        selector.close()
        if process is not None:
            if not done:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
                process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()


def valid_passphrase(value):
    return isinstance(value, bytes) and re.fullmatch(rb"[0-9a-f]{64}", value) is not None


class Keyring:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700)

    def call(self, *arguments, passphrase=None, data=None):
        if passphrase is not None and (data is not None or not valid_passphrase(passphrase)):
            raise KitError("invalid_passphrase_transport")
        argv = ["/usr/bin/gpg", "--no-options", "--batch", "--no-tty", "--homedir", str(self.directory),
                "--auto-key-locate", "clear", "--no-auto-key-retrieve", "--no-symkey-cache",
                "--no-encrypt-to", "--status-fd", "2"]
        if passphrase is not None:
            argv += ["--pinentry-mode", "loopback", "--passphrase-fd", "0"]
            data = passphrase + b"\n"
        result, output, diagnostics = run_command([*argv, *arguments], input_bytes=data)
        if len(output) > MAX_KIT_BYTES or len(diagnostics) > MAX_DIAGNOSTICS:
            raise KitError("gpg_operation_failed")
        if result:
            status_lines = diagnostics.splitlines()
            if "--decrypt" in arguments and any(
                    line == b"[GNUPG:] DECRYPTION_FAILED" or line == b"[GNUPG:] BADMDC"
                    or line.startswith((b"[GNUPG:] NO_SECKEY ", b"[GNUPG:] NODATA "))
                    for line in status_lines):
                raise KitError("decryption_rejected")
            raise KitError("gpg_operation_failed")
        if "--decrypt" in arguments:
            markers = set(diagnostics.splitlines())
            required = {b"[GNUPG:] DECRYPTION_OKAY", b"[GNUPG:] GOODMDC",
                        b"[GNUPG:] END_DECRYPTION", b"[GNUPG:] DECRYPTION_INFO 2 9 0"}
            if not required <= markers:
                raise KitError("decryption_integrity_unconfirmed")
        return output

    def fingerprints(self, secret=False):
        data = self.call("--with-colons", "--with-fingerprint",
                         "--list-secret-keys" if secret else "--list-keys")
        values = [line.split(b":")[9].decode("ascii") for line in data.splitlines()
                  if line.startswith(b"fpr:")]
        if (len(values) > 2 or len(set(values)) != len(values)
                or any(re.fullmatch(r"[0-9A-F]{40}", value) is None for value in values)):
            raise KitError("unexpected_key_identity")
        return values

    def generate(self, passphrase):
        self.call("--quick-generate-key", "Synthetic recovery <fixture@example.invalid>",
                  "rsa2048", "cert,sign", "1d", passphrase=passphrase)
        values = self.fingerprints()
        if len(values) != 1:
            raise KitError("synthetic_primary_missing")
        self.call("--quick-add-key", values[0], "rsa2048", "encrypt", "1d", passphrase=passphrase)
        values = self.fingerprints(secret=True)
        if len(values) != 2:
            raise KitError("synthetic_subkey_missing")
        return values

    def close(self):
        try:
            result, _, _ = run_command(["/usr/bin/gpgconf", "--homedir", str(self.directory),
                                        "--kill", "gpg-agent"], timeout=5, limit=MAX_DIAGNOSTICS)
            return result == 0
        except KitError:
            return False


def _validate_recovery(recovery):
    if (type(recovery) is not dict
            or set(recovery) != {"schema", "synthetic_only", "inner_passphrase", "fingerprints", "canary_sha256"}
            or recovery["schema"] != SCHEMA or recovery["synthetic_only"] is not True
            or not isinstance(recovery["inner_passphrase"], str)
            or re.fullmatch(r"[0-9a-f]{64}", recovery["inner_passphrase"]) is None
            or type(recovery["fingerprints"]) is not list or len(recovery["fingerprints"]) != 2
            or any(not isinstance(v, str) or re.fullmatch(r"[0-9A-F]{40}", v) is None
                   for v in recovery["fingerprints"])
            or len(set(recovery["fingerprints"])) != 2
            or recovery["canary_sha256"] != digest(CANARY)):
        raise KitError("invalid_recovery_metadata")


def make_package(files, recovery):
    """Only synthetic inputs supplied by this fixture are supported."""
    try:
        _validate_recovery(recovery)
        if type(files) is not dict or set(files) != FILES - {"recovery.json"}:
            raise KitError("invalid_package_scope")
        payloads = {**files, "recovery.json": canonical(recovery)}
        if any(not isinstance(v, bytes) or not 0 < len(v) <= MAX_MEMBER_BYTES for v in payloads.values()):
            raise KitError("package_member_bound")
        manifest = {"schema": SCHEMA, "synthetic_only": True,
                    "files": {name: {"size_bytes": len(data), "sha256": digest(data)}
                              for name, data in sorted(payloads.items())}}
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for name, data in sorted({**payloads, "manifest.json": canonical(manifest)}.items()):
                member = tarfile.TarInfo(name)
                member.size, member.mode = len(data), 0o600
                archive.addfile(member, io.BytesIO(data))
        bundle = output.getvalue()
        if len(bundle) > MAX_KIT_BYTES:
            raise KitError("package_bound")
        return bundle, digest(canonical(manifest))
    except KitError:
        raise
    except Exception:
        raise KitError("package_failed") from None


def unpack(bundle, expected_hash):
    """Check every member in memory; never invoke tar extraction."""
    try:
        if (not isinstance(bundle, bytes) or not 0 < len(bundle) <= MAX_KIT_BYTES
                or not isinstance(expected_hash, str) or re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None):
            raise KitError("invalid_package")
        contents = {}
        with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:") as archive:
            for member in archive:
                if (len(contents) >= len(FILES) + 1 or member.name not in FILES | {"manifest.json"}
                        or member.name in contents or not member.isfile() or member.mode != 0o600
                        or not 0 < member.size <= MAX_MEMBER_BYTES):
                    raise KitError("invalid_member")
                data = archive.extractfile(member).read(member.size + 1)
                if len(data) != member.size:
                    raise KitError("invalid_member")
                contents[member.name] = data
        if set(contents) != FILES | {"manifest.json"}:
            raise KitError("missing_package_member")
        encoded = contents.pop("manifest.json")
        if digest(encoded) != expected_hash:
            raise KitError("manifest_integrity")
        manifest = strict_json(encoded)
        if (type(manifest) is not dict or set(manifest) != {"schema", "synthetic_only", "files"}
                or manifest["schema"] != SCHEMA or manifest["synthetic_only"] is not True
                or type(manifest["files"]) is not dict or set(manifest["files"]) != FILES):
            raise KitError("invalid_manifest")
        for name, data in contents.items():
            item = manifest["files"][name]
            if (type(item) is not dict or type(item.get("size_bytes")) is not int
                    or item != {"size_bytes": len(data), "sha256": digest(data)}):
                raise KitError("payload_integrity")
        recovery = strict_json(contents["recovery.json"])
        _validate_recovery(recovery)
        if contents["instructions.txt"] != INSTRUCTIONS:
            raise KitError("invalid_fixture_instructions")
        return contents, recovery
    except KitError:
        raise
    except Exception:
        raise KitError("package_parse_failed") from None


def transform(keyring, payload, passphrase, *, encrypt=False):
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_KIT_BYTES:
        raise KitError("transform_input_bound")
    # The enforced wrapper places this entire temporary tree on private tmpfs.
    with tempfile.TemporaryDirectory(prefix="transform-", dir=keyring.directory.parent) as temporary:
        path = Path(temporary) / "input.bin"
        fixture.write_private(path, payload)
        if encrypt:
            return keyring.call("--cipher-algo", "AES256", "--s2k-mode", "3",
                                "--s2k-digest-algo", "SHA256", "--s2k-count", str(S2K_COUNT),
                                "--compress-algo", "none", "--set-filename", "",
                                "--output", "-", "--symmetric", str(path), passphrase=passphrase)
        return keyring.call("--max-output", str(MAX_KIT_BYTES), "--output", "-", "--decrypt", str(path),
                            passphrase=passphrase)


def _must_fail(operation):
    try:
        operation()
    except KitError as error:
        if error.args == ("decryption_rejected",):
            return True
        raise
    raise KitError("negative_check_unexpected_success")


def roundtrip():
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="rbx-gpg-emergency-") as temporary:
        base = Path(temporary)
        rings = []
        report = None
        try:
            source, producer, outer, wrong_inner, recovered = [Keyring(base / name) for name in
                                                             ("source", "producer", "outer", "wrong-inner", "recovered")]
            rings.extend((source, producer, outer, wrong_inner, recovered))
            version = source.call("--version").splitlines()[0].decode("ascii")
            inner_secret, outer_secret = secrets.token_hex(32).encode(), secrets.token_hex(32).encode()
            if inner_secret == outer_secret:
                raise KitError("independent_secret_collision")
            fingerprints = source.generate(inner_secret)
            plain = base / "canary.txt"
            fixture.write_private(plain, CANARY)
            canary = source.call("--trust-model", "always", "--cipher-algo", "AES256", "--compress-algo", "none",
                                 "--recipient", fingerprints[1] + "!", "--output", "-", "--encrypt", str(plain))
            files = {"private-key.gpg": source.call("--export-options", "backup", "--export-secret-keys",
                                                     fingerprints[0], passphrase=inner_secret),
                     "public-key.gpg": source.call("--export", fingerprints[0]),
                     "revocation.txt": fixture.bounded_read(source.directory / "openpgp-revocs.d" /
                                                              (fingerprints[0] + ".rev"), MAX_MEMBER_BYTES),
                     "credential.gpg": canary, "instructions.txt": INSTRUCTIONS}
            recovery = {"schema": SCHEMA, "synthetic_only": True, "inner_passphrase": inner_secret.decode("ascii"),
                        "fingerprints": fingerprints, "canary_sha256": digest(CANARY)}
            bundle, manifest_hash = make_package(files, recovery)
            if producer.fingerprints(secret=True) or outer.fingerprints(secret=True):
                raise KitError("outer_ring_has_private_keys")
            ciphertext = transform(producer, bundle, outer_secret, encrypt=True)
            if not source.close():
                raise KitError("source_agent_cleanup_failed")
            shutil.rmtree(source.directory)
            plain.unlink()
            del files, recovery, inner_secret, fingerprints, bundle, canary
            # The outside wrapping secret is held by this one controller only.
            # This proves mechanics, not independent custody or threshold release.
            negative_outer = _must_fail(lambda: transform(outer, ciphertext, secrets.token_hex(32).encode()))
            changed = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
            negative_tamper = _must_fail(lambda: transform(outer, changed, outer_secret))
            negative_truncation = _must_fail(lambda: transform(outer, ciphertext[:len(ciphertext) // 2], outer_secret))
            opened = transform(outer, ciphertext, outer_secret)
            files, recovery = unpack(opened, manifest_hash)
            if recovered.fingerprints(secret=True) or wrong_inner.fingerprints(secret=True):
                raise KitError("recovery_ring_not_fresh")
            negative_missing = _must_fail(lambda: transform(outer, files["credential.gpg"],
                                                           recovery["inner_passphrase"].encode("ascii")))
            for ring in (wrong_inner, recovered):
                ring.call("--import-options", "restore", "--import", data=files["private-key.gpg"])
                if ring.fingerprints(secret=True) != recovery["fingerprints"]:
                    raise KitError("recovered_identity_mismatch")
            negative_inner = _must_fail(lambda: transform(wrong_inner, files["credential.gpg"], secrets.token_hex(32).encode()))
            result = transform(recovered, files["credential.gpg"], recovery["inner_passphrase"].encode("ascii"))
            if result != CANARY or digest(result) != recovery["canary_sha256"]:
                raise KitError("recovered_canary_mismatch")
            report = {"schema": SCHEMA, "synthetic_only": True, "gpg_version": version,
                      "wrapping_cipher": "AES256-CFB+MDC", "s2k_mode": 3, "s2k_digest": "SHA256",
                      "s2k_count": S2K_COUNT, "outer_secret_entropy_bits": 256,
                      "outer_secret_in_package": False, "inner_passphrase_inside_envelope": True,
                      "source_keyring_removed": not source.directory.exists(), "fresh_recovery_keyring": True,
                      "primary_and_encryption_subkey_restored": True, "canary_decryption_match": True,
                      "wrong_outer_rejected": negative_outer, "wrong_inner_rejected": negative_inner,
                      "missing_key_rejected": negative_missing, "tamper_rejected": negative_tamper,
                      "truncation_rejected": negative_truncation, "package_members": len(files),
                      "ciphertext_size_bytes": len(ciphertext), "elapsed_seconds": round(time.monotonic() - started, 3),
                      "production_key_accessed": False, "provider_integration_tested": False,
                      "threshold_implemented": False, "independent_custody_tested": False,
                      "production_security_support_qualified": False, "cross_version_tested": False}
        finally:
            results = [ring.close() for ring in rings]
            if report is not None:
                report["agent_cleanup_confirmed"] = len(results) == 5 and all(results)
    report["temporary_files_removed"] = not base.exists()
    return report


def main(argv=None):
    if list(sys.argv[1:] if argv is None else argv):
        print(json.dumps({"error": "synthetic_fixture_accepts_no_arguments"}), file=sys.stderr)
        return 2
    try:
        report = roundtrip()
        print(json.dumps(report, sort_keys=True))
        return 0 if report["agent_cleanup_confirmed"] and report["temporary_files_removed"] else 1
    except Exception:
        print(json.dumps({"error": "synthetic_emergency_kit_failed"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
