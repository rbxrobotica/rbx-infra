#!/usr/bin/env python3
"""Prepare a private, offline Headscale adoption bundle; never deploy it.

Inputs must be operator-attested stopped sources or private consistent copies.
Repeated file checks detect observed changes, but cannot prove an atomic live
snapshot. The receipt records no native validation, deployment or traffic proof.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import sys

import yaml

_SPEC = importlib.util.spec_from_file_location(
    "rbx_headscale_file_preflight", Path(__file__).with_name("headscale_preflight.py")
)
preflight = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(preflight)

SCHEMA_VERSION = "headscale-candidate-bundle-v1"
HEADSCALE_VERSION = "0.29.3"


class PreparationError(Exception):
    """Only a fixed error code may leave the implementation."""


def _path(value):
    path = Path(value).expanduser().absolute()
    if ".." in path.parts or not path.name:
        raise PreparationError("invalid_path")
    return path


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _expected(value):
    if type(value) is not str or not re.fullmatch(r"[0-9A-Fa-f]{64}", value):
        raise PreparationError("expected_sha256_invalid")
    return value.lower()


def _fingerprint(info):
    return preflight._fingerprint(info) + (info.st_uid, info.st_gid, info.st_nlink)


def _sample(path):
    before = path.lstat()
    data = preflight.read_file(path)
    after = path.lstat()
    if _fingerprint(before) != _fingerprint(after):
        raise PreparationError("input_changed")
    return {
        "path": path,
        "data": data,
        "sha256": _sha256(data),
        "fingerprint": _fingerprint(after),
    }


def _check_inputs(samples):
    for original in samples:
        current = _sample(original["path"])
        if (
            current["sha256"] != original["sha256"]
            or current["fingerprint"] != original["fingerprint"]
        ):
            raise PreparationError("input_changed")


def _parsed_config(data):
    value = yaml.load(
        data.decode("utf-8", errors="strict"), Loader=preflight.BoundedSafeLoader
    )
    if type(value) is not dict:
        raise PreparationError("config_not_mapping")
    return value


def _same_value(left, right):
    """Preserve YAML types; Python's True == 1 must never hide a change."""
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(
            _same_value(left[key], right[key]) for key in left
        )
    if type(left) is list:
        return len(left) == len(right) and all(
            _same_value(a, b) for a, b in zip(left, right, strict=True)
        )
    if type(left) is float:
        return (
            math.isfinite(left) and math.isfinite(right) and left.hex() == right.hex()
        )
    if left is None or type(left) in {str, int, bool}:
        return left == right
    # Explicit YAML tags, binary data, dates and sets are outside the bounded
    # configuration contract. Do not guess at their native Headscale meaning.
    raise PreparationError("unsupported_config_value")


def _open_directory(path):
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            following = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = following
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _identity(info):
    return info.st_dev, info.st_ino


def _assert_private_parent(descriptor):
    info = os.fstat(descriptor)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise PreparationError("output_parent_not_owned_private_0700")
    return _identity(info)


def _check_output_location(
    path, parent, parent_identity, directory, directory_identity
):
    checked = _open_directory(path.parent)
    try:
        if _assert_private_parent(checked) != parent_identity:
            raise PreparationError("output_parent_changed")
    finally:
        os.close(checked)
    info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
    if (
        not stat.S_ISDIR(info.st_mode)
        or _identity(info) != directory_identity
        or _identity(os.fstat(directory)) != directory_identity
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise PreparationError("output_directory_changed")


def _write_file(directory, name, data, created):
    descriptor = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory,
    )
    try:
        created[name] = _identity(os.fstat(descriptor))
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(data):
            size = os.write(descriptor, data[offset:])
            if size <= 0:
                raise PreparationError("bundle_write_incomplete")
            offset += size
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _check_file(directory, name, data, identity):
    descriptor = os.open(
        name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or _identity(info) != identity
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size != len(data)
            or info.st_nlink != 1
        ):
            raise PreparationError("bundle_file_changed")
        blocks = []
        remaining = len(data) + 1
        while remaining:
            block = os.read(descriptor, min(65536, remaining))
            if not block:
                break
            blocks.append(block)
            remaining -= len(block)
        if b"".join(blocks) != data or _fingerprint(
            os.fstat(descriptor)
        ) != _fingerprint(info):
            raise PreparationError("bundle_file_changed")
    finally:
        os.close(descriptor)


def _cleanup(parent, name, directory, directory_identity, created):
    """Remove only the inode identities created by this invocation."""
    complete = True
    if directory is not None:
        for filename, identity in reversed(tuple(created.items())):
            try:
                info = os.stat(filename, dir_fd=directory, follow_symlinks=False)
                if _identity(info) != identity:
                    complete = False
                    continue
                os.unlink(filename, dir_fd=directory)
            except FileNotFoundError:
                pass
            except Exception:
                complete = False
    if directory_identity is not None:
        try:
            if (
                _identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
                != directory_identity
            ):
                complete = False
            else:
                os.rmdir(name, dir_fd=parent)
        except FileNotFoundError:
            pass
        except Exception:
            complete = False
    return complete


def _receipt():
    return {
        "schema_version": SCHEMA_VERSION,
        "bundle_format": SCHEMA_VERSION,
        "headscale_version": HEADSCALE_VERSION,
        "status": "blocked",
        "adoption_only": True,
        "live_changes": False,
        "native_validation": False,
        "policy_preserved": False,
        "traffic_semantics_verified": False,
        "snapshot_reference_override": False,
        "live_reference_path_verified": False,
        "input_consistency": {
            "operator_attestation_required": True,
            "stopped_sources_or_private_copies_required": True,
            "live_atomic_snapshot": False,
            "fingerprints_rechecked": False,
        },
    }


def prepare(
    current_config,
    policy,
    candidate_config,
    expect_config_sha256,
    expect_policy_sha256,
    output_dir,
    *,
    policy_reference_path,
):
    """Prepare exclusively new files; return only a sanitized receipt or error."""
    report = _receipt()
    parent = directory = None
    directory_identity = None
    created = {}
    output = None
    try:
        expected_config = _expected(expect_config_sha256)
        expected_policy = _expected(expect_policy_sha256)
        inputs = tuple(
            _path(item) for item in (current_config, policy, candidate_config)
        )
        output = _path(output_dir)
        if any(
            output == item or output in item.parents or item in output.parents
            for item in inputs
        ):
            raise PreparationError("output_contains_or_overlaps_input")
        samples = tuple(_sample(path) for path in inputs)
        current, policy_sample, candidate = samples
        if (
            current["sha256"] != expected_config
            or policy_sample["sha256"] != expected_policy
        ):
            raise PreparationError("input_sha256_mismatch")
        if (
            not Path(policy_reference_path).is_absolute()
            or ".." in Path(policy_reference_path).parts
        ):
            raise PreparationError("policy_reference_path_must_be_explicit_absolute")
        observed = preflight.audit(*inputs, policy_reference_path=policy_reference_path)
        if observed["status"] != "observation_only":
            raise PreparationError("file_preflight_blocked")
        for label, sample in zip(
            ("config", "policy", "candidate_config"), samples, strict=True
        ):
            if observed["files"][label]["sha256"] != sample["sha256"]:
                raise PreparationError("input_changed")
        if not _same_value(
            _parsed_config(current["data"]), _parsed_config(candidate["data"])
        ):
            raise PreparationError("adoption_requires_complete_semantic_identity")
        _check_inputs(samples)
        parent = _open_directory(output.parent)
        parent_identity = _assert_private_parent(parent)
        try:
            os.stat(output.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise PreparationError("output_already_exists")
        os.mkdir(output.name, mode=0o700, dir_fd=parent)
        # Record creation before opening, so open/write failures can remove the
        # new directory without ever deleting a pre-existing path.
        directory_identity = _identity(
            os.stat(output.name, dir_fd=parent, follow_symlinks=False)
        )
        directory = os.open(
            output.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
        )
        os.fchmod(directory, 0o700)
        _check_output_location(
            output, parent, parent_identity, directory, directory_identity
        )
        _write_file(directory, "config.yaml", current["data"], created)
        _write_file(directory, "policy.hujson", policy_sample["data"], created)
        for name, sample in (
            ("config.yaml", current),
            ("policy.hujson", policy_sample),
        ):
            _check_file(directory, name, sample["data"], created[name])
        _check_inputs(samples)
        _check_output_location(
            output, parent, parent_identity, directory, directory_identity
        )
        report.update(
            status="prepared",
            policy_preserved=True,
            snapshot_reference_override=True,
            hashes={
                "current_config_sha256": current["sha256"],
                "candidate_config_sha256": candidate["sha256"],
                "policy_sha256": policy_sample["sha256"],
                "bundled_config_sha256": current["sha256"],
                "bundled_policy_sha256": policy_sample["sha256"],
            },
            file_observation_schema=preflight.SCHEMA_VERSION,
        )
        report["input_consistency"]["fingerprints_rechecked"] = True
        receipt_bytes = (
            json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        # Receipt is the final file, and is not a deployment/native-validation
        # marker. A caller must inspect the process result as well as the files.
        _write_file(directory, "receipt.json", receipt_bytes, created)
        os.fsync(directory)
        os.fsync(parent)
        _check_file(directory, "receipt.json", receipt_bytes, created["receipt.json"])
        _check_inputs(samples)
        _check_output_location(
            output, parent, parent_identity, directory, directory_identity
        )
        return report
    except Exception as error:
        complete = _cleanup(
            parent,
            output.name if output else "",
            directory,
            directory_identity,
            created,
        )
        report = _receipt()
        report.update(
            error_code=(
                error.args[0]
                if type(error) is PreparationError
                else "preparation_failed"
            ),
            cleanup_complete=complete,
        )
        return report
    finally:
        for descriptor in (directory, parent):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    # A final close cannot be retried safely after EINTR; all
                    # content, fsync and cleanup checks have already completed.
                    pass


def main(argv=None):
    try:
        parser = preflight.SanitizedParser(description=__doc__, add_help=False)
        for argument in (
            "current-config",
            "policy",
            "candidate-config",
            "expect-config-sha256",
            "expect-policy-sha256",
            "output-dir",
            "policy-reference-path",
        ):
            parser.add_argument("--" + argument, required=True)
        args = parser.parse_args(argv)
        report = prepare(
            args.current_config,
            args.policy,
            args.candidate_config,
            args.expect_config_sha256,
            args.expect_policy_sha256,
            args.output_dir,
            policy_reference_path=args.policy_reference_path,
        )
    except Exception:
        report = _receipt()
        report.update(
            error_code="invalid_arguments_or_preparation_failed", cleanup_complete=True
        )
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["status"] == "prepared" else 2


if __name__ == "__main__":
    sys.exit(main())
