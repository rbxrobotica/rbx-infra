#!/usr/bin/env python3
"""Preserve selected DNS state files as owner-encrypted archives, never migrate.

All source analysis and archive construction happen in memory. Reports contain
source metadata and aggregate comparisons, never state attributes or credentials.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile
import time

MAX_FILE = 4 * 1024 * 1024
MAX_ARCHIVE = 20 * 1024 * 1024
GPG = "/usr/bin/gpg"
LABELS = ("principal.tfstate", "principal.tfstate.backup",
          "historical.tfstate", "historical.tfstate.backup")
ZONES = frozenset(("initelo.com", "iteruno.com", "kulinaryos.com", "merovelis.com",
                   "rbx.ia.br", "rbxsystems.ch", "satwake.com", "strategos.gr",
                   "verentir.com", "zugbrief.com"))


class SafeError(Exception):
    """Only fixed public reason codes may be emitted by the CLI."""


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


@dataclass
class Snapshot:
    metadata: dict
    raw: bytes = field(repr=False)


def unchanged(a, b):
    return (a.st_dev, a.st_ino, a.st_size, a.st_mtime_ns, a.st_ctime_ns) == (
        b.st_dev, b.st_ino, b.st_size, b.st_mtime_ns, b.st_ctime_ns)


def snapshot(path):
    path = Path(path).absolute()
    if not path.name.startswith("terraform.tfstate"):
        raise SafeError("unexpected_source_filename")
    if (path.parent / ".terraform.tfstate.lock.info").exists():
        raise SafeError("source_lock_present")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_FILE:
                raise SafeError("source_type_or_size")
            if before.st_uid != os.getuid():
                raise SafeError("source_owner_mismatch")
            raw = stream.read(MAX_FILE + 1)
            after = os.fstat(stream.fileno())
        if len(raw) != before.st_size or not unchanged(before, after) or not unchanged(after, path.stat()):
            raise SafeError("source_changed_during_capture")
    except SafeError:
        raise
    except OSError:
        raise SafeError("source_unavailable_or_symlink") from None
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict) or parsed.get("version") != 4:
            raise SafeError("unsupported_state_format")
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SafeError("source_not_valid_json_state") from None
    return Snapshot({"path": str(path), "bytes": len(raw), "sha256": digest(raw),
                     "mtime_ns": before.st_mtime_ns, "mode": oct(stat.S_IMODE(before.st_mode)),
                     "uid": before.st_uid}, raw)


def binding_index(data):
    bindings, zone_names = {}, set()
    for resource in data.get("resources", []):
        if resource.get("mode") != "managed":
            continue
        base = (resource.get("module", ""), resource.get("type"), resource.get("name"))
        for instance in resource.get("instances", []):
            attrs = instance.get("attributes") or {}
            key = base + (json.dumps(instance.get("index_key"), sort_keys=True),)
            if key in bindings:
                raise SafeError("duplicate_state_binding")
            bindings[key] = attrs.get("id")
            if resource.get("type") == "powerdns_zone" and isinstance(attrs.get("name"), str):
                zone_names.add(attrs["name"].rstrip(".").lower())
    return bindings, zone_names


def compare(principal, historical):
    a, b = json.loads(principal.raw), json.loads(historical.raw)
    try:
        ai, az = binding_index(a)
        bi, bz = binding_index(b)
        shared = ai.keys() & bi.keys()
        conflicts = sum(ai[k] != bi[k] for k in shared)
        same_lineage = bool(a.get("lineage")) and a.get("lineage") == b.get("lineage")
        newer = type(a.get("serial")) is int and type(b.get("serial")) is int and a["serial"] > b["serial"]
        superset = bool(bi) and bi.keys() <= ai.keys()
        qualified = same_lineage and newer and superset and conflicts == 0 and az == ZONES and bool(bz) and bz < az
        return {"same_lineage": same_lineage, "principal_serial_newer": newer,
                "historical_bindings_preserved_in_principal": superset,
                "shared_binding_conflicts": conflicts,
                "principal_zone_corpus_matches_ten_observed_zones": az == ZONES,
                "historical_zone_corpus_is_proper_subset": bool(bz) and bz < az,
                "principal_canonical_candidate_supported": qualified,
                "all_record_values_live_reconciled": False,
                "raw_state_or_attribute_values_emitted": False}
    except (AttributeError, TypeError, KeyError):
        raise SafeError("unsupported_state_structure") from None


def make_archive(snapshots):
    if set(snapshots) != set(LABELS):
        raise SafeError("archive_source_set_mismatch")
    manifest = {"format": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
                "files": {label: item.metadata for label, item in snapshots.items()}}
    manifest_bytes = canonical(manifest)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, raw in [("manifest.json", manifest_bytes)] + [(k, v.raw) for k, v in snapshots.items()]:
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode, entry.mtime = len(raw), 0o600, 0
            archive.addfile(entry, io.BytesIO(raw))
    raw = stream.getvalue()
    if len(raw) > MAX_ARCHIVE:
        raise SafeError("archive_too_large")
    return raw, manifest, digest(manifest_bytes)


def validate_archive(raw, expected_manifest_sha256):
    if not 0 < len(raw) <= MAX_ARCHIVE:
        raise SafeError("decrypted_archive_size")
    expected = {"manifest.json", *LABELS}
    files = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            for entry in archive:
                if entry.name not in expected or entry.name in files or not entry.isfile():
                    raise SafeError("archive_member_unsafe_or_duplicate")
                if not 0 < entry.size <= MAX_FILE:
                    raise SafeError("archive_member_size")
                files[entry.name] = archive.extractfile(entry).read(MAX_FILE + 1)
    except (tarfile.TarError, OSError):
        raise SafeError("archive_format") from None
    if set(files) != expected or digest(files["manifest.json"]) != expected_manifest_sha256:
        raise SafeError("archive_manifest_or_members_mismatch")
    try:
        manifest = json.loads(files["manifest.json"])
        for label in LABELS:
            metadata = manifest["files"][label]
            if len(files[label]) != metadata["bytes"] or digest(files[label]) != metadata["sha256"]:
                raise SafeError("restored_state_checksum_mismatch")
            if json.loads(files[label]).get("version") != 4:
                raise SafeError("restored_state_format")
    except (ValueError, KeyError, TypeError, AttributeError):
        raise SafeError("restored_manifest_structure") from None
    return {"files_restored_in_memory": len(LABELS), "all_source_checksums_match": True,
            "plaintext_files_written": False, "live_state_overwritten": False}


def encrypt_archive(raw, recipient):
    if re.fullmatch(r"[0-9A-F]{40,64}", recipient) is None:
        raise SafeError("recipient_must_be_full_fingerprint")
    p = subprocess.run([GPG, "--batch", "--no-auto-key-retrieve", "--recipient", recipient,
                        "--encrypt", "--output", "-"], input=raw,
                       capture_output=True, timeout=40, check=False)
    if p.returncode or not p.stdout or len(p.stdout) > MAX_ARCHIVE:
        raise SafeError("gpg_encryption_failed")
    return p.stdout


def verify(path, manifest_hash):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_ARCHIVE:
                raise SafeError("ciphertext_type_or_size")
            raw = stream.read(MAX_ARCHIVE + 1)
        if len(raw) != info.st_size:
            raise SafeError("ciphertext_changed_during_read")
    except OSError:
        raise SafeError("ciphertext_unavailable_or_symlink") from None
    started = time.monotonic()
    p = subprocess.run([GPG, "--batch", "--no-auto-key-retrieve", "--decrypt", "--output", "-"],
                       input=raw, capture_output=True, timeout=40, check=False)
    if p.returncode:
        raise SafeError("gpg_decryption_failed")
    receipt = validate_archive(p.stdout, manifest_hash)
    return {**receipt, "ciphertext_sha256": digest(raw),
            "restore_elapsed_seconds": round(time.monotonic() - started, 3)}


def capture(principal, historical):
    paths = dict(zip(LABELS, [principal, Path(str(principal) + ".backup"),
                             historical, Path(str(historical) + ".backup")]))
    snapshots = {label: snapshot(path) for label, path in paths.items()}
    return snapshots, compare(snapshots["principal.tfstate"], snapshots["historical.tfstate"])


def write_exclusive(path, raw):
    path = Path(path)
    if not path.name.endswith(".tar.gpg"):
        raise SafeError("backup_output_extension")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise SafeError("backup_directory_not_private_owned")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        raise SafeError("backup_output_already_exists") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "backup", "verify"))
    parser.add_argument("--principal", type=Path)
    parser.add_argument("--historical", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--recipient")
    parser.add_argument("--manifest-sha256")
    args = parser.parse_args()
    try:
        if args.action == "verify":
            if not args.output or not re.fullmatch(r"[0-9a-f]{64}", args.manifest_sha256 or ""):
                raise SafeError("verify_arguments")
            result = {"action": "verify", **verify(args.output, args.manifest_sha256)}
        else:
            if not args.principal or not args.historical:
                raise SafeError("source_arguments")
            snapshots, comparison = capture(args.principal, args.historical)
            result = {"action": args.action, "sources": {k: v.metadata for k, v in snapshots.items()},
                      "comparison": comparison, "source_state_changed": False}
            if args.action == "backup":
                if not args.output or not args.recipient:
                    raise SafeError("backup_arguments")
                raw, _, manifest_hash = make_archive(snapshots)
                encrypted = encrypt_archive(raw, args.recipient)
                for item in snapshots.values():
                    if snapshot(Path(item.metadata["path"])).metadata != item.metadata:
                        raise SafeError("source_changed_before_backup_commit")
                write_exclusive(args.output, encrypted)
                result.update(backup_path=str(args.output), ciphertext_bytes=len(encrypted),
                              ciphertext_sha256=digest(encrypted), manifest_sha256=manifest_hash,
                              plaintext_files_written=False, migration_performed=False)
        print(json.dumps({"completed": True, **result}))
        return 0
    except SafeError as error:
        print(json.dumps({"completed": False, "error": error.args[0], "state_values_emitted": False}))
    except Exception as error:
        print(json.dumps({"completed": False, "error": "unexpected_failure",
                          "error_type": type(error).__name__, "state_values_emitted": False}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
