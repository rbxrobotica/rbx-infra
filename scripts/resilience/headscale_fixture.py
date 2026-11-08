#!/usr/bin/env python3
"""Synthetic capture/restore mechanics, not a production Headscale exporter.

The CLI accepts no paths, keys, source configuration or backend arguments.
Only this module's disposable model and dummy identities are generated. Its
SQLite model is not the Headscale schema; no Headscale binary is invoked.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
import tarfile
import tempfile
import time

try:
    from . import local_fixture as crypto
except ImportError:
    import local_fixture as crypto

SCHEMA = "rbx.resilience.headscale-shaped-fixture.v1"
MAX_FILE_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = 2 * 1024 * 1024
CAPTURE_SECONDS = 5
BASE_FILES = frozenset({"config.yaml", "policy.hujson", "noise.key", "tls-account.json"})
DATABASE = "source.sqlite"
SNAPSHOT = "state.sqlite"
IDENTITY = b"SYNTHETIC-NOISE-IDENTITY-NOT-A-PRIVATE-KEY\n"
DERP = b"SYNTHETIC-DERP-IDENTITY-NOT-A-PRIVATE-KEY\n"
USERS = [(1, "synthetic-owner")]
NODES = [(1, "synthetic-workstation", 1), (2, "synthetic-phone", 1)]


class CaptureError(Exception):
    """Only fixed, non-sensitive failure codes leave the mechanism."""


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def strict_json(value):
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise CaptureError("duplicate_json_key")
            result[key] = item
        return result
    def constant(_):
        raise CaptureError("invalid_json_number")
    return json.loads(value, object_pairs_hook=pairs, parse_constant=constant)


def configuration(embedded_derp):
    # JSON is also a YAML subset. References are logical synthetic names only.
    return {"synthetic_only": True, "server_url": "https://control.example.invalid",
            "database": {"type": "sqlite", "path": SNAPSHOT},
            "noise": {"private_key_path": "noise.key"},
            "policy": {"mode": "file", "path": "policy.hujson"},
            "tls_account": "tls-account.json",
            "derp": {"enabled": embedded_derp,
                     "private_key_path": "derp.key" if embedded_derp else None}}


def file_names(embedded_derp):
    if type(embedded_derp) is not bool:
        raise CaptureError("invalid_profile")
    return BASE_FILES | ({"derp.key"} if embedded_derp else set())


@dataclass
class Source:
    directory: Path
    writer: sqlite3.Connection
    embedded_derp: bool
    expected: dict

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.writer.close()


def make_source(directory, embedded_derp=False):
    """Generate the only supported source: an owned disposable fixture."""
    names = file_names(embedded_derp)
    directory = Path(directory)
    directory.mkdir(mode=0o700)
    writer = None
    try:
        payloads = {"config.yaml": canonical(configuration(embedded_derp)),
                    "policy.hujson": canonical({"grants": [], "synthetic_only": True}),
                    "noise.key": IDENTITY,
                    "tls-account.json": canonical({"synthetic_only": True, "account": "dummy"})}
        if embedded_derp:
            payloads["derp.key"] = DERP
        for name, value in payloads.items():
            crypto.write_private(directory / name, value)
        database = directory / DATABASE
        writer = sqlite3.connect(database)
        os.chmod(database, 0o600)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA foreign_keys=ON")
        writer.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        writer.execute("CREATE TABLE nodes(id INTEGER PRIMARY KEY, name TEXT NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id))")
        writer.executemany("INSERT INTO users VALUES (?,?)", USERS)
        writer.executemany("INSERT INTO nodes VALUES (?,?,?)", NODES)
        writer.commit()
        expected = {name: digest(payloads[name]) for name in names}
        return Source(directory, writer, embedded_derp, expected)
    except Exception:
        if writer is not None:
            writer.close()
        shutil.rmtree(directory)
        raise CaptureError("source_generation_failed") from None


def _deadline(deadline):
    if time.monotonic() >= deadline:
        raise CaptureError("capture_deadline")


def _open_source(source):
    descriptor = os.open(source.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    info = os.fstat(descriptor)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        os.close(descriptor)
        raise CaptureError("unsafe_source_directory")
    return descriptor


def _scan(descriptor, names):
    allowed = set(names) | {DATABASE, DATABASE + "-wal", DATABASE + "-shm"}
    seen = set()
    with os.scandir(descriptor) as entries:
        for count, entry in enumerate(entries, 1):
            if count > len(allowed) or entry.name not in allowed:
                raise CaptureError("unexpected_source_entry")
            info = entry.stat(follow_symlinks=False)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
                raise CaptureError("unsafe_source_file")
            seen.add(entry.name)
    if not (set(names) | {DATABASE}) <= seen:
        raise CaptureError("missing_source_file")


def _read_file(descriptor, name):
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
    try:
        before = os.fstat(handle)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.getuid() or stat.S_IMODE(before.st_mode) != 0o600
                or not 0 < before.st_size <= MAX_FILE_BYTES):
            raise CaptureError("unsafe_source_file")
        chunks = bytearray()
        while len(chunks) <= MAX_FILE_BYTES:
            chunk = os.read(handle, min(65536, MAX_FILE_BYTES + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
        after = os.fstat(handle)
        signature = lambda s: (s.st_dev, s.st_ino, s.st_uid, s.st_gid, s.st_mode,
                                s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if signature(before) != signature(after) or len(chunks) != before.st_size:
            raise CaptureError("source_changed")
        return bytes(chunks), signature(after)
    finally:
        os.close(handle)


def _model(connection):
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.execute("PRAGMA query_only=ON")
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise CaptureError("sqlite_integrity")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise CaptureError("sqlite_relationships")
    schema = connection.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
    users = connection.execute("SELECT id,name FROM users ORDER BY id").fetchall()
    nodes = connection.execute("SELECT id,name,user_id FROM nodes ORDER BY id").fetchall()
    if users != USERS or nodes != NODES:
        raise CaptureError("fixture_model_mismatch")
    return {"schema_sha256": digest(canonical(schema)),
            "users_sha256": digest(canonical(users)), "nodes_sha256": digest(canonical(nodes)),
            "users": len(users), "nodes": len(nodes)}


def _snapshot(source, deadline):
    """Bounded online backup; committed WAL is included, pending writes are not."""
    reader = snapshot = None
    try:
        reader = sqlite3.connect((source.directory / DATABASE).absolute().as_uri() + "?mode=ro",
                                 uri=True, timeout=0.05)
        page_size = reader.execute("PRAGMA page_size").fetchone()[0]
        pages = reader.execute("PRAGMA page_count").fetchone()[0]
        if not 0 < page_size * pages <= MAX_FILE_BYTES:
            raise CaptureError("sqlite_size")
        snapshot = sqlite3.connect(":memory:")
        def progress(status, remaining, total):
            _deadline(deadline)
            if total < 0 or total * page_size > MAX_FILE_BYTES:
                raise CaptureError("sqlite_size")
        _deadline(deadline)
        reader.backup(snapshot, pages=16, progress=progress, sleep=0.01)
        _deadline(deadline)
        model = _model(snapshot)
        data = snapshot.serialize()
        if not 0 < len(data) <= MAX_FILE_BYTES:
            raise CaptureError("sqlite_size")
        return data, model
    finally:
        if snapshot is not None:
            snapshot.close()
        if reader is not None:
            reader.close()


def _bundle(payloads, manifest):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in sorted({**payloads, "manifest.json": canonical(manifest)}.items()):
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(data), 0o600
            archive.addfile(member, io.BytesIO(data))
    value = output.getvalue()
    if not 0 < len(value) <= MAX_BUNDLE_BYTES:
        raise CaptureError("bundle_size")
    return value


def capture(source):
    """Capture a generated fixture; no guarantee about arbitrary/live sources."""
    descriptor = None
    started = datetime.now(timezone.utc).isoformat()
    deadline = time.monotonic() + CAPTURE_SECONDS
    try:
        names = file_names(source.embedded_derp)
        if set(source.expected) != names:
            raise CaptureError("invalid_source_baseline")
        descriptor = _open_source(source)
        _scan(descriptor, names)
        initial = {name: _read_file(descriptor, name) for name in sorted(names)}
        if any(digest(data) != source.expected[name] for name, (data, _) in initial.items()):
            raise CaptureError("source_baseline_mismatch")
        if strict_json(initial["config.yaml"][0]) != configuration(source.embedded_derp):
            raise CaptureError("invalid_fixture_configuration")
        database_before = os.stat(DATABASE, dir_fd=descriptor, follow_symlinks=False)
        snapshot, model = _snapshot(source, deadline)
        _scan(descriptor, names)
        if any(_read_file(descriptor, name) != initial[name] for name in sorted(names)):
            raise CaptureError("source_changed")
        database_after = os.stat(DATABASE, dir_fd=descriptor, follow_symlinks=False)
        if (database_before.st_dev, database_before.st_ino) != (database_after.st_dev, database_after.st_ino):
            raise CaptureError("source_changed")
        _deadline(deadline)
        payloads = {name: data for name, (data, _) in initial.items()}
        payloads[SNAPSHOT] = snapshot
        if sum(map(len, payloads.values())) > MAX_TOTAL_BYTES:
            raise CaptureError("capture_size")
        manifest = {"schema": SCHEMA, "synthetic_only": True,
                    "headscale_native_schema": False, "embedded_derp": source.embedded_derp,
                    "consistency": "sqlite-online-backup+fixture-owned-stable-files",
                    "started_utc": started, "finished_utc": datetime.now(timezone.utc).isoformat(),
                    "files": {name: {"size_bytes": len(data), "sha256": digest(data), "mode": "0600"}
                              for name, data in sorted(payloads.items())}, "model": model}
        return _bundle(payloads, manifest), digest(canonical(manifest))
    except CaptureError:
        raise
    except Exception:
        raise CaptureError("capture_failed") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _unpack(bundle, expected_manifest_hash):
    if (not isinstance(bundle, bytes) or not 0 < len(bundle) <= MAX_BUNDLE_BYTES
            or not isinstance(expected_manifest_hash, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected_manifest_hash) is None):
        raise CaptureError("invalid_bundle")
    contents = {}
    allowed = BASE_FILES | {SNAPSHOT, "derp.key", "manifest.json"}
    with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:") as archive:
        for member in archive:
            if (len(contents) >= len(allowed) or member.name not in allowed
                    or member.name in contents or not member.isfile()
                    or not 0 < member.size <= MAX_FILE_BYTES or member.mode != 0o600):
                raise CaptureError("invalid_member")
            stream = archive.extractfile(member)
            data = stream.read(member.size + 1)
            if len(data) != member.size:
                raise CaptureError("invalid_member")
            contents[member.name] = data
    if "manifest.json" not in contents or digest(contents["manifest.json"]) != expected_manifest_hash:
        raise CaptureError("manifest_integrity")
    manifest = strict_json(contents.pop("manifest.json"))
    required_keys = {"schema", "synthetic_only", "headscale_native_schema", "embedded_derp",
                     "consistency", "started_utc", "finished_utc", "files", "model"}
    if (type(manifest) is not dict or set(manifest) != required_keys
            or manifest["schema"] != SCHEMA or manifest["synthetic_only"] is not True
            or manifest["headscale_native_schema"] is not False
            or manifest["consistency"] != "sqlite-online-backup+fixture-owned-stable-files"):
        raise CaptureError("invalid_manifest")
    names = file_names(manifest["embedded_derp"]) | {SNAPSHOT}
    if (set(contents) != names or type(manifest["files"]) is not dict
            or set(manifest["files"]) != names):
        raise CaptureError("missing_capture_member")
    if sum(map(len, contents.values())) > MAX_TOTAL_BYTES:
        raise CaptureError("capture_size")
    for name, data in contents.items():
        record = manifest["files"][name]
        if (type(record) is not dict or type(record.get("size_bytes")) is not int
                or record != {"sha256": digest(data), "size_bytes": len(data), "mode": "0600"}):
            raise CaptureError("payload_integrity")
    model = manifest["model"]
    if (type(model) is not dict
            or set(model) != {"schema_sha256", "users_sha256", "nodes_sha256", "users", "nodes"}
            or any(type(model.get(k)) is not int or model[k] < 1 for k in ("users", "nodes"))
            or any(not isinstance(model.get(k), str)
                   or re.fullmatch(r"[0-9a-f]{64}", model[k]) is None
                   for k in ("schema_sha256", "users_sha256", "nodes_sha256"))):
        raise CaptureError("invalid_model_manifest")
    if (strict_json(contents["config.yaml"]) != configuration(manifest["embedded_derp"])
            or strict_json(contents["policy.hujson"]) != {"grants": [], "synthetic_only": True}
            or contents["noise.key"] != IDENTITY
            or strict_json(contents["tls-account.json"]) != {"synthetic_only": True, "account": "dummy"}
            or (manifest["embedded_derp"] and contents["derp.key"] != DERP)):
        raise CaptureError("fixture_identity_or_configuration")
    try:
        start = datetime.fromisoformat(manifest["started_utc"])
        end = datetime.fromisoformat(manifest["finished_utc"])
        if (start.utcoffset() != timedelta(0) or end.utcoffset() != timedelta(0)
                or end < start or (end - start).total_seconds() > CAPTURE_SECONDS):
            raise CaptureError("invalid_capture_interval")
    except (ValueError, TypeError):
        raise CaptureError("invalid_capture_interval") from None
    return contents, manifest


def restore(bundle, expected_manifest_hash, destination):
    """Validate every archive entry before private materialization; never extract."""
    destination = Path(destination)
    created = False
    connection = None
    try:
        if destination.exists() or destination.is_symlink():
            raise CaptureError("restore_destination_exists")
        contents, manifest = _unpack(bundle, expected_manifest_hash)
        destination.mkdir(mode=0o700)
        created = True
        for name, data in sorted(contents.items()):
            crypto.write_private(destination / name, data)
        # serialize() preserves WAL-mode header bits; an immutable restored copy
        # has no writers or external WAL dependency, so it is safe to inspect.
        connection = sqlite3.connect((destination / SNAPSHOT).absolute().as_uri() + "?mode=ro&immutable=1", uri=True)
        model = _model(connection)
        if model != manifest["model"]:
            raise CaptureError("restored_model_mismatch")
        return {"sqlite_integrity": "ok", "relationships_verified": True,
                "snapshot_model_verified": True, "nodes_restored": model["nodes"],
                "configuration_policy_identity_bytes_match": True,
                "embedded_derp": manifest["embedded_derp"],
                "restored_file_count": len(contents), "headscale_runtime_tested": False}
    except CaptureError:
        if created:
            shutil.rmtree(destination)
        raise
    except Exception:
        if created:
            shutil.rmtree(destination)
        raise CaptureError("restore_failed") from None
    finally:
        if connection is not None:
            connection.close()


def roundtrip(embedded_derp=False):
    """Use fresh ephemeral synthetic keys, local ciphertext and no source reuse."""
    file_names(embedded_derp)
    with tempfile.TemporaryDirectory(prefix="rbx-headscale-shaped-") as temporary:
        base = Path(temporary)
        producer = crypto.Keyring(base / "producer")
        recovery = crypto.Keyring(base / "recovery")
        report = None
        try:
            recipient = recovery.generate()
            public = base / "recipient.asc"
            crypto.write_private(public, recovery.run("--export", recipient))
            producer.run("--import", str(public))
            if any(line.startswith((b"sec:", b"ssb:"))
                   for line in producer.run("--with-colons", "--list-secret-keys").splitlines()):
                raise CaptureError("producer_private_keys")
            with make_source(base / "source", embedded_derp) as source:
                bundle, manifest_hash = capture(source)
            plain = base / "capture.tar"
            crypto.write_private(plain, bundle)
            encrypted = base / "archive.gpg"
            producer.run("--trust-model", "always", "--no-encrypt-to", "--cipher-algo", "AES256",
                         "--compress-algo", "none", "--recipient", recipient + "!",
                         "--output", str(encrypted), "--encrypt", str(plain))
            cipher = crypto.bounded_read(encrypted, MAX_BUNDLE_BYTES)
            retrieved = base / "retrieved.gpg"
            crypto.write_private(retrieved, cipher)
            shutil.rmtree(base / "source")
            plain.unlink()
            del bundle
            decrypted = base / "restored.tar"
            crypto.decrypt(retrieved, digest(cipher), recovery, decrypted)
            verification = restore(crypto.bounded_read(decrypted, MAX_BUNDLE_BYTES), manifest_hash, base / "restored")
            report = {"schema": SCHEMA, "synthetic_only": True, "transport": "local-copy",
                      "production_data_accessed": False, "production_capture_implemented": False,
                      "independent_custody_tested": False, "provider_integration_tested": False,
                      "source_removed_before_restore": True, "producer_private_keys": False,
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
        print(json.dumps({"error": "synthetic_fixture_accepts_no_arguments"}), file=sys.stderr)
        return 2
    try:
        report = roundtrip()
        print(json.dumps(report, sort_keys=True))
        return 0 if report["agent_cleanup_confirmed"] and report["temporary_files_removed"] else 1
    except Exception:
        print(json.dumps({"error": "synthetic_capture_restore_failed"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
