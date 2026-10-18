#!/usr/bin/env python3
"""Bounded observations of explicit Headscale files, never a policy engine.

This tool neither invokes Headscale nor opens its database/API. It reads only
the paths supplied on the command line and emits hashes, counts, static finding
codes and coarse flags. A successful observation proves neither deployment nor
the authorization or denial of any traffic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from urllib.parse import urlsplit

import yaml

SCHEMA_VERSION = "headscale-file-preflight-v1"
MAX_FILE_BYTES = 1024 * 1024
MAX_YAML_NODES = 20000
MAX_YAML_DEPTH = 64


class ObservationError(Exception):
    """Contains only a fixed finding code, never input data or parser errors."""


class BoundedSafeLoader(yaml.SafeLoader):
    def __init__(self, stream):
        super().__init__(stream)
        self.node_count = 0
        self.depth = 0

    def compose_node(self, parent, index):
        self.node_count += 1
        self.depth += 1
        try:
            if (
                self.node_count > MAX_YAML_NODES
                or self.depth > MAX_YAML_DEPTH
                or self.check_event(yaml.AliasEvent)
            ):
                raise ObservationError("unsupported_yaml_structure")
            return super().compose_node(parent, index)
        finally:
            self.depth -= 1

    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if type(key) is not str or key in result:
                raise ObservationError("ambiguous_yaml_mapping")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _fingerprint(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def read_file(path):
    """Open every path component without following links; never write a file."""
    path = Path(path).expanduser().absolute()
    if ".." in path.parts:
        raise ObservationError("path_traversal")
    directory = descriptor = None
    try:
        directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
        for component in path.parts[1:-1]:
            next_directory = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory,
            )
            os.close(directory)
            directory = next_directory
        before = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise ObservationError("not_regular_file")
        if before.st_size > MAX_FILE_BYTES:
            raise ObservationError("file_too_large")
        descriptor = os.open(
            path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _fingerprint(opened) != _fingerprint(
            before
        ):
            raise ObservationError("file_changed")
        blocks = []
        size = 0
        while size <= MAX_FILE_BYTES:
            block = os.read(descriptor, min(65536, MAX_FILE_BYTES + 1 - size))
            if not block:
                break
            blocks.append(block)
            size += len(block)
        if size > MAX_FILE_BYTES:
            raise ObservationError("file_too_large")
        if _fingerprint(os.fstat(descriptor)) != _fingerprint(opened) or _fingerprint(
            os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        ) != _fingerprint(opened):
            raise ObservationError("file_changed")
        return b"".join(blocks)
    except ObservationError:
        raise
    except Exception:
        raise ObservationError("file_unreadable_or_symlink") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ObservationError("ambiguous_json_mapping")
        result[key] = value
    return result


def _json_constant(_value):
    raise ObservationError("invalid_json_constant")


def _finding(report, code, *, blocking=True):
    finding = {"code": code, "blocking": blocking}
    if finding not in report["findings"]:
        report["findings"].append(finding)


def _input(report, label, path, *, policy=False):
    info = {
        "read": False,
        "parsed": False,
        "sha256": None,
        "size_bytes": None,
        "top_level_field_count": None,
    }
    report["files"][label] = info
    try:
        raw = read_file(path)
        info.update(
            read=True, sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw)
        )
        if not raw.strip():
            raise ObservationError("empty_file")
        text = raw.decode("utf-8", errors="strict")
        if policy:
            value = json.loads(
                text, object_pairs_hook=_json_pairs, parse_constant=_json_constant
            )
        else:
            value = yaml.load(text, Loader=BoundedSafeLoader)
        if type(value) is not dict:
            raise ObservationError("root_not_mapping")
        info.update(parsed=True, top_level_field_count=len(value))
        return value
    except ObservationError as error:
        _finding(report, f"{label}_{error.args[0]}")
    except Exception:
        _finding(report, f"{label}_unsupported_or_unparseable")
    return None


def _boolean(mapping, key):
    value = mapping.get(key)
    return value if type(value) is bool else None


def _string_list(value, *, empty=True):
    return (
        type(value) is list
        and (empty or bool(value))
        and all(type(item) is str and bool(item.strip()) for item in value)
    )


def _path_matches(reference, explicit):
    if type(reference) is not str or not reference or not Path(reference).is_absolute():
        return False
    return os.path.normpath(reference) == str(Path(explicit).expanduser().absolute())


def _config(report, value, policy_path, label, policy_reference_path=None):
    policy = value.get("policy")
    if type(policy) is not dict:
        policy = {}
        _finding(report, f"{label}_policy_section_missing_or_malformed")
    mode = policy.get("mode")
    reference = policy.get("path")
    flags = {
        "policy_mode_present": "mode" in policy,
        "policy_mode_file": mode == "file",
        "policy_path_present": type(reference) is str and bool(reference.strip()),
        "policy_path_matches_explicit_input": _path_matches(reference, policy_path),
        "policy_path_matches_expected_reference": _path_matches(
            reference,
            policy_path if policy_reference_path is None else policy_reference_path,
        ),
    }
    if mode != "file":
        _finding(report, f"{label}_file_policy_mode_missing_or_unsupported")
    if not flags["policy_path_present"]:
        _finding(report, f"{label}_policy_path_missing")
    elif not flags["policy_path_matches_expected_reference"]:
        _finding(report, f"{label}_policy_path_mismatch_or_relative")

    derp = value.get("derp")
    derp = derp if type(derp) is dict else {}
    server = derp.get("server")
    server = server if type(server) is dict else {}
    urls, paths = derp.get("urls"), derp.get("paths")
    valid_urls, valid_paths = _string_list(urls), _string_list(paths)
    public_map = None
    if valid_urls:
        public_map = False
        try:
            for item in urls:
                parsed = urlsplit(item)
                if parsed.scheme not in {"https", "http"} or not parsed.hostname:
                    raise ValueError
                if (
                    parsed.hostname.casefold()
                    in {"controlplane.tailscale.com", "login.tailscale.com"}
                    and parsed.path.rstrip("/") == "/derpmap/default"
                ):
                    public_map = True
        except Exception:
            public_map = None
            valid_urls = False
    flags["derp"] = {
        "embedded_enabled": _boolean(server, "enabled"),
        "embedded_auto_add": _boolean(server, "automatically_add_embedded_derp_region"),
        "external_map_urls_configured": bool(urls) if valid_urls else None,
        "tailscale_public_map_configured": public_map,
        "local_map_files_configured": bool(paths) if valid_paths else None,
        "auto_update_enabled": _boolean(derp, "auto_update_enabled"),
    }
    if flags["derp"]["embedded_enabled"] is None or not valid_urls or not valid_paths:
        _finding(report, f"{label}_derp_profile_incomplete_or_malformed")
    if (
        "automatically_add_embedded_derp_region" in server
        and flags["derp"]["embedded_auto_add"] is None
    ) or (
        "auto_update_enabled" in derp and flags["derp"]["auto_update_enabled"] is None
    ):
        _finding(report, f"{label}_derp_boolean_malformed")
    dns = value.get("dns")
    dns = dns if type(dns) is dict else {}
    nameservers = dns.get("nameservers")
    nameservers = nameservers if type(nameservers) is dict else {}
    globals_ = nameservers.get("global")
    split = nameservers.get("split")
    search = dns.get("search_domains")
    flags["dns"] = {
        "magic_dns": _boolean(dns, "magic_dns"),
        "override_local_dns_present": "override_local_dns" in dns,
        "override_local_dns": _boolean(dns, "override_local_dns"),
        "global_nameservers_configured": (
            bool(globals_) if _string_list(globals_) else None
        ),
        "split_nameservers_configured": bool(split) if type(split) is dict else None,
        "search_domains_configured": bool(search) if _string_list(search) else None,
    }
    if (
        flags["dns"]["magic_dns"] is None
        or ("override_local_dns" in dns and flags["dns"]["override_local_dns"] is None)
        or not _string_list(globals_)
        or (
            "split" in nameservers
            and (
                type(split) is not dict
                or not all(_string_list(item) for item in split.values())
            )
        )
        or ("search_domains" in dns and not _string_list(search))
    ):
        _finding(report, f"{label}_dns_flags_incomplete_or_malformed")
    return flags


def _policy(report, value):
    flags = {
        "network_rule_containers_present": False,
        "acl_rule_count": None,
        "grant_rule_count": None,
        "network_rule_count": None,
        "explicit_empty_network_rules": False,
        "broad_wildcard_rule_observed": False,
        "potential_permissive_default": False,
        "authorization_proven": False,
    }
    if value is None:
        flags["potential_permissive_default"] = True
        return flags
    present = [name for name in ("acls", "grants") if name in value]
    flags["network_rule_containers_present"] = bool(present)
    if not present:
        flags["potential_permissive_default"] = True
        _finding(report, "policy_network_rule_containers_missing")
        return flags
    total = 0
    malformed = False
    for name, count_name in (
        ("acls", "acl_rule_count"),
        ("grants", "grant_rule_count"),
    ):
        if name not in value:
            continue
        rules = value[name]
        if type(rules) is not list:
            malformed = True
            continue
        flags[count_name] = len(rules)
        total += len(rules)
        for rule in rules:
            if (
                type(rule) is not dict
                or not _string_list(rule.get("src"), empty=False)
                or not _string_list(rule.get("dst"), empty=False)
            ):
                malformed = True
                continue
            if name == "acls" and rule.get("action") != "accept":
                malformed = True
            if name == "grants" and not _string_list(rule.get("ip"), empty=False):
                malformed = True
            broad_src = any(item in {"*", "0.0.0.0/0", "::/0"} for item in rule["src"])
            broad_dst = any(
                item in {"*", "*:*", "0.0.0.0/0", "0.0.0.0/0:*", "::/0", "::/0:*"}
                for item in rule["dst"]
            )
            if broad_src and broad_dst:
                flags["broad_wildcard_rule_observed"] = True
    if malformed:
        _finding(report, "policy_network_rule_container_malformed_or_unsupported")
    else:
        flags["network_rule_count"] = total
        flags["explicit_empty_network_rules"] = total == 0
        if total == 0:
            _finding(report, "policy_explicit_empty_rules_require_semantic_review")
    if flags["broad_wildcard_rule_observed"]:
        _finding(report, "policy_broad_wildcard_rule_requires_review")
    return flags


def _report():
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "blocked",
        "observation_only": True,
        "deployment_verified": False,
        "traffic_semantics_verified": False,
        "mutations_performed": False,
        "snapshot_reference_override": False,
        "live_reference_path_verified": False,
        "files": {},
        "observations": {},
        "findings": [],
    }


def audit(
    config_path, policy_path, candidate_config_path=None, *, policy_reference_path=None
):
    report = _report()
    try:
        if policy_reference_path is not None:
            report["snapshot_reference_override"] = True
            expected_reference = Path(policy_reference_path)
            if not expected_reference.is_absolute() or ".." in expected_reference.parts:
                _finding(report, "policy_reference_path_must_be_explicit_absolute")
                return report
        current = _input(report, "config", config_path)
        policy = _input(report, "policy", policy_path, policy=True)
        if current is not None:
            report["observations"]["config"] = _config(
                report, current, policy_path, "config", policy_reference_path
            )
        report["observations"]["policy"] = _policy(report, policy)
        if candidate_config_path is not None:
            candidate = _input(report, "candidate_config", candidate_config_path)
            if candidate is not None:
                report["observations"]["candidate_config"] = _config(
                    report,
                    candidate,
                    policy_path,
                    "candidate_config",
                    policy_reference_path,
                )
            if current is not None and candidate is not None:
                current_policy = current.get("policy")
                candidate_policy = candidate.get("policy")
                preserved = (
                    type(current_policy) is dict
                    and type(candidate_policy) is dict
                    and all(
                        key in current_policy
                        and key in candidate_policy
                        and current_policy[key] == candidate_policy[key]
                        for key in ("mode", "path")
                    )
                )
                comparison = {
                    "policy_reference_preserved": preserved,
                    "derp_profile_drift": current.get("derp") != candidate.get("derp"),
                    "dns_flags_or_profile_drift": current.get("dns")
                    != candidate.get("dns"),
                }
                report["observations"]["comparison"] = comparison
                for key, unsafe in (
                    ("policy_reference_not_preserved", not preserved),
                    ("derp_profile_drift", comparison["derp_profile_drift"]),
                    (
                        "dns_flags_or_profile_drift",
                        comparison["dns_flags_or_profile_drift"],
                    ),
                ):
                    if unsafe:
                        _finding(report, f"candidate_{key}")
    except Exception:
        _finding(report, "observation_failed")
    if not any(item["blocking"] for item in report["findings"]):
        report["status"] = "observation_only"
    return report


class SanitizedParser(argparse.ArgumentParser):
    def error(self, _message):
        raise ObservationError("invalid_arguments")


def main(argv=None):
    try:
        # Even malformed CLI requests return the same JSON contract; argparse
        # must never echo an unknown argument or a user-supplied path.
        parser = SanitizedParser(description=__doc__, add_help=False)
        parser.add_argument("--config", required=True)
        parser.add_argument("--policy", required=True)
        parser.add_argument("--candidate-config")
        parser.add_argument("--policy-reference-path")
        args = parser.parse_args(argv)
        report = audit(
            args.config,
            args.policy,
            args.candidate_config,
            policy_reference_path=args.policy_reference_path,
        )
    except Exception:
        report = _report()
        _finding(report, "invalid_arguments_or_observation_failed")
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["status"] == "observation_only" else 2


if __name__ == "__main__":
    sys.exit(main())
