#!/usr/bin/env python3
"""Exercise synthetic SQLite/config encryption and restoration, without a backend.

The CLI accepts no source paths, credentials or provider configuration. All data
and the short-lived recovery key are generated inside a temporary directory.
Use run-local-fixture.sh for enforced network and host-filesystem isolation.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile

MAX_FILE_BYTES = 512 * 1024
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
FILES = frozenset({"state.sqlite", "application.json"})
ROWS = [("fixture-1", "synthetic workstation", 1),
        ("fixture-2", "synthetic phone", 0)]
APPLICATION = {"server_url": "https://control.example.invalid", "synthetic_only": True}
SCHEMA = "rbx.resilience.local-fixture.v1"


class FixtureError(Exception):
    """Only fixed, non-sensitive failure descriptions leave the fixture."""


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_private(path, data):
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        stream.write(data)


def bounded_read(path, maximum=MAX_ARTIFACT_BYTES):
    if path.is_symlink() or not path.is_file():
        raise FixtureError("Expected a regular fixture file")
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if not data or len(data) > maximum:
        raise FixtureError("Fixture file exceeds its size bounds")
    return data


def command(argv):
    # Never inherit AWS/PG/GPG/session variables or emit raw subprocess errors.
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env={"PATH": "/usr/bin:/bin", "LANG": "C"},
                                timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        raise FixtureError("Synthetic cryptographic operation failed") from None
    if result.returncode or len(result.stdout) > MAX_ARTIFACT_BYTES:
        raise FixtureError("Synthetic cryptographic operation failed")
    return result.stdout


class Keyring:
    def __init__(self, directory):
        self.directory = directory
        directory.mkdir(mode=0o700)

    def run(self, *arguments):
        return command(["gpg", "--no-options", "--batch", "--no-tty",
                        "--homedir", str(self.directory), "--auto-key-locate", "clear",
                        "--no-auto-key-retrieve", *arguments])

    def generate(self):
        self.run("--pinentry-mode", "loopback", "--passphrase", "",
                 "--quick-generate-key", "Synthetic recovery <fixture@example.invalid>",
                 "rsa2048", "encrypt", "1d")
        listing = self.run("--with-colons", "--list-keys").decode("ascii")
        fingerprints = [line.split(":")[9] for line in listing.splitlines()
                        if line.startswith("fpr:")]
        if len(fingerprints) != 1 or not re.fullmatch(r"[0-9A-F]{40}", fingerprints[0]):
            raise FixtureError("Synthetic recipient was not established")
        return fingerprints[0]

    def close(self):
        # Kill only this fixture's agent before removing its temporary keyring.
        try:
            command(["gpgconf", "--homedir", str(self.directory), "--kill", "gpg-agent"])
            return True
        except FixtureError:
            return False


def create_fixture(directory):
    directory.mkdir(mode=0o700)
    source = directory / "source.sqlite"
    connection = sqlite3.connect(source)
    try:
        connection.execute("CREATE TABLE nodes(id TEXT PRIMARY KEY, label TEXT, enabled INTEGER)")
        connection.executemany("INSERT INTO nodes VALUES (?,?,?)", ROWS)
        connection.commit()
        # Capture using the SQLite backup API, not a bare copy of a live DB.
        snapshot = sqlite3.connect(directory / "state.sqlite")
        try:
            connection.backup(snapshot)
        finally:
            snapshot.close()
    finally:
        connection.close()
    os.chmod(directory / "state.sqlite", 0o600)
    source.unlink()
    write_private(directory / "application.json", canonical(APPLICATION))
    manifest = {"schema": SCHEMA, "synthetic_only": True,
                "files": {name: {"sha256": digest(data), "size_bytes": len(data)}
                          for name in sorted(FILES)
                          for data in [bounded_read(directory / name, MAX_FILE_BYTES)]},
                "rows_sha256": digest(canonical(ROWS))}
    return manifest


def make_bundle(directory, manifest, destination):
    # Fixed member names; no recursive archives or externally supplied paths.
    with destination.open("xb") as stream:
        os.chmod(destination, 0o600)
        with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for name in sorted(FILES):
                data = bounded_read(directory / name, MAX_FILE_BYTES)
                member = tarfile.TarInfo(name)
                member.size, member.mode = len(data), 0o600
                archive.addfile(member, io.BytesIO(data))
            data = canonical(manifest)
            member = tarfile.TarInfo("manifest.json")
            member.size, member.mode = len(data), 0o600
            archive.addfile(member, io.BytesIO(data))
    bounded_read(destination)


def restore_bundle(bundle, expected_manifest_hash, destination):
    """Validate every member before writing; never extract arbitrary tar paths."""
    if destination.exists():
        raise FixtureError("Restore destination already exists")
    try:
        with tarfile.open(fileobj=io.BytesIO(bounded_read(bundle)), mode="r:") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            if len(names) != 3 or set(names) != FILES | {"manifest.json"}:
                raise FixtureError("Unexpected or duplicate archive member")
            if any(not member.isfile() or member.size < 1 or member.size > MAX_FILE_BYTES
                   for member in members):
                raise FixtureError("Archive member exceeds its type or size bounds")
            contents = {member.name: archive.extractfile(member).read() for member in members}
        if digest(contents["manifest.json"]) != expected_manifest_hash:
            raise FixtureError("Manifest integrity mismatch")
        manifest = json.loads(contents["manifest.json"])
        if (manifest.get("schema") != SCHEMA or manifest.get("synthetic_only") is not True
                or set(manifest.get("files", {})) != FILES):
            raise FixtureError("Unexpected fixture manifest")
        for name in FILES:
            if manifest["files"][name] != {"sha256": digest(contents[name]),
                                           "size_bytes": len(contents[name])}:
                raise FixtureError("Captured file integrity mismatch")
    except (tarfile.TarError, ValueError, TypeError, AttributeError, KeyError):
        raise FixtureError("Invalid fixture archive") from None
    try:
        destination.mkdir(mode=0o700)
        for name in FILES:
            write_private(destination / name, contents[name])
        # immutable+mode=ro prevent journal creation or repair of restored state.
        database = sqlite3.connect((destination / "state.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True)
        try:
            if database.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise FixtureError("Restored SQLite integrity failed")
            rows = database.execute("SELECT id,label,enabled FROM nodes ORDER BY id").fetchall()
        finally:
            database.close()
        application = json.loads(contents["application.json"])
        if (rows != ROWS or digest(canonical(rows)) != manifest.get("rows_sha256")
                or application != APPLICATION):
            raise FixtureError("Restored application state mismatch")
        return {"sqlite_integrity": "ok", "rows_restored": len(rows),
                "logical_state_match": True, "application_config_match": True}
    except (OSError, sqlite3.Error, ValueError, FixtureError):
        shutil.rmtree(destination)
        raise FixtureError("Restored application verification failed") from None


def decrypt(ciphertext, expected_hash, recovery, destination):
    if digest(bounded_read(ciphertext)) != expected_hash:
        raise FixtureError("Ciphertext integrity mismatch")
    if destination.exists():
        raise FixtureError("Decryption destination already exists")
    try:
        recovery.run("--max-output", str(MAX_ARTIFACT_BYTES), "--output", str(destination),
                     "--decrypt", str(ciphertext))
        os.chmod(destination, 0o600)
        bounded_read(destination)
    except (FixtureError, OSError):
        destination.unlink(missing_ok=True)
        raise FixtureError("Synthetic decryption failed") from None


def roundtrip():
    for tool in ("gpg", "gpgconf"):
        if shutil.which(tool, path="/usr/bin:/bin") is None:
            raise FixtureError("GnuPG and gpgconf are required")
    with tempfile.TemporaryDirectory(prefix="rbx-fixture-") as temporary:
        base = Path(temporary)
        producer = Keyring(base / "producer")
        recovery = Keyring(base / "recovery")
        report = None
        try:
            fingerprint = recovery.generate()
            public = base / "recipient.asc"
            write_private(public, recovery.run("--armor", "--export", fingerprint))
            producer.run("--import", str(public))
            if any(line.startswith((b"sec:", b"ssb:"))
                   for line in producer.run("--with-colons", "--list-secret-keys").splitlines()):
                raise FixtureError("Producer contains private key material")
            manifest = create_fixture(base / "source")
            bundle = base / "capture.tar"
            make_bundle(base / "source", manifest, bundle)
            encrypted = base / "archive.gpg"
            producer.run("--trust-model", "always", "--no-encrypt-to", "--cipher-algo", "AES256",
                         "--compress-algo", "none", "--recipient", fingerprint + "!",
                         "--output", str(encrypted), "--encrypt", str(bundle))
            os.chmod(encrypted, 0o600)
            cipher = bounded_read(encrypted)
            # The transport is deliberately a local ciphertext copy, not B2/S3.
            retrieved = base / "retrieved.gpg"
            write_private(retrieved, cipher)
            # Destroy source plaintext before restore; no live source comparison.
            shutil.rmtree(base / "source")
            bundle.unlink()
            restored_bundle = base / "restored.tar"
            decrypt(retrieved, digest(cipher), recovery, restored_bundle)
            verification = restore_bundle(restored_bundle, digest(canonical(manifest)), base / "restored")
            report = {"schema": SCHEMA, "synthetic_only": True, "transport": "local-copy",
                      "production_data_accessed": False, "provider_integration_tested": False,
                      "headscale_runtime_tested": False, "retention_enforcement_tested": False,
                      "producer_private_keys": False, "ciphertext_readback_match": True,
                      "ciphertext_size_bytes": len(cipher), **verification}
        finally:
            producer_closed = producer.close()
            recovery_closed = recovery.close()
            if report is not None:
                report["agent_cleanup_confirmed"] = producer_closed and recovery_closed
    report["temporary_files_removed"] = not base.exists()
    return report


def main(argv=None):
    if list(sys.argv[1:] if argv is None else argv):
        print(json.dumps({"error": "This synthetic fixture accepts no arguments"}), file=sys.stderr)
        return 2
    try:
        report = roundtrip()
        print(json.dumps(report, sort_keys=True))
        return 0 if report["agent_cleanup_confirmed"] and report["temporary_files_removed"] else 1
    except (FixtureError, OSError, sqlite3.Error, ValueError):
        print(json.dumps({"error": "Synthetic fixture failed; no recovery capability established"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
