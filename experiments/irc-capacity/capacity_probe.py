#!/usr/bin/env python3
"""Bounded read-only IRC capacity metadata; never open application state."""

import argparse
import datetime
import ipaddress
import json
import os
from pathlib import Path
import stat
import subprocess
import time


CONTAINERS = ("rbx-ergo", "rbx-znc")
STATE_ROOT = Path("/srv/rbx/irc")


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=12)
    if result.returncode:
        raise RuntimeError("command_unavailable")
    if len(result.stdout) > 128 * 1024:
        raise RuntimeError("command_output_bound")
    return result.stdout.strip()


def metadata_tree(root):
    if STATE_ROOT.resolve() != STATE_ROOT or not root.is_relative_to(STATE_ROOT):
        raise ValueError("unexpected_state_root")
    result = {"exists": root.exists(), "regular_files": 0, "bytes": 0,
              "complete": True, "metadata_errors": 0,
              "symlinks_skipped": 0, "entry_bound_reached": False,
              "database_bytes": 0, "wal_bytes": 0, "shm_bytes": 0,
              "oldest_mtime_epoch": None, "newest_mtime_epoch": None}
    if root.is_symlink():
        result["complete"] = False
        result["symlinks_skipped"] += 1
        return result
    if not root.exists():
        return result
    seen = 0
    def onerror(error):
        result["complete"] = False
        result["metadata_errors"] += 1
    for parent, dirs, names in os.walk(root, followlinks=False, onerror=onerror):
        seen += len(dirs) + len(names)
        if seen > 10000:
            result["entry_bound_reached"] = True
            result["complete"] = False
            return result
        safe_dirs = []
        for name in dirs:
            if (Path(parent) / name).is_symlink():
                result["symlinks_skipped"] += 1
            else:
                safe_dirs.append(name)
        dirs[:] = safe_dirs
        for name in names:
            try:
                info = (Path(parent) / name).lstat()
            except OSError:
                onerror(None)
                continue
            if stat.S_ISLNK(info.st_mode):
                result["symlinks_skipped"] += 1
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            result["regular_files"] += 1
            result["bytes"] += info.st_size
            field = ("wal_bytes" if name.endswith("-wal") else
                     "shm_bytes" if name.endswith("-shm") else
                     "database_bytes" if name.endswith((".db", ".sqlite", ".sqlite3")) else None)
            if field:
                result[field] += info.st_size
            old, new = result["oldest_mtime_epoch"], result["newest_mtime_epoch"]
            result["oldest_mtime_epoch"] = min(old, info.st_mtime) if old else info.st_mtime
            result["newest_mtime_epoch"] = max(new, info.st_mtime) if new else info.st_mtime
    return result


def cgroup(pid):
    for row in Path(f"/proc/{pid}/cgroup").read_text().splitlines():
        if row.startswith("0::"):
            root = Path("/sys/fs/cgroup").resolve()
            target = (root / row[3:].lstrip("/")).resolve()
            if target == root or not target.is_relative_to(root):
                raise ValueError("unexpected_cgroup")
            return target
    raise ValueError("cgroup_v2_unavailable")


def numbers(path):
    return {row.split()[0]: int(row.split()[1])
            for row in path.read_text().splitlines() if len(row.split()) == 2}


def sample(pid):
    group = cgroup(pid)
    cpu = numbers(group / "cpu.stat")
    sample = {"monotonic_seconds": time.monotonic(),
              "memory_current_bytes": int((group / "memory.current").read_text()),
              "cpu_usage_usec": cpu["usage_usec"],
              "pids_current": int((group / "pids.current").read_text()),
              "memory_events": numbers(group / "memory.events")}
    peak = group / "memory.peak"
    sample["memory_peak_bytes"] = int(peak.read_text()) if peak.exists() else None
    network = {"rx_bytes": 0, "tx_bytes": 0}
    try:
        network_lines = Path(f"/proc/{pid}/net/dev").read_text().splitlines()[2:]
    except OSError:
        sample["network_namespace_nonloopback"] = {"available": False}
        sample["irc_connections"] = {"available": False}
        return sample
    for line in network_lines:
        interface, fields = line.split(":", 1)
        if interface.strip() != "lo":
            parts = fields.split()
            network["rx_bytes"] += int(parts[0])
            network["tx_bytes"] += int(parts[8])
    sample["network_namespace_nonloopback"] = network
    connections = {"established_6667": 0, "established_6697": 0}
    for table in ("tcp", "tcp6"):
        path = Path(f"/proc/{pid}/net/{table}")
        if not path.exists():
            continue
        for row in path.read_text().splitlines()[1:]:
            cols = row.split()
            port = int(cols[1].rsplit(":", 1)[1], 16)
            if cols[3] == "01" and port in (6667, 6697):
                connections[f"established_{port}"] += 1
    sample["irc_connections"] = connections
    return sample


def binding_class(value):
    address = ipaddress.ip_address(value)
    return ("cgnat_not_a_trust_assertion" if address in ipaddress.ip_network("100.64.0.0/10")
            else "loopback" if address.is_loopback else "all_interfaces" if address.is_unspecified
            else "private" if address.is_private else "specific_public")


def inspect(name):
    fields = {"running": ".State.Running", "pid": ".State.Pid",
              "started_at": ".State.StartedAt", "restart_count": ".RestartCount",
              "memory_limit_bytes": ".HostConfig.Memory", "nano_cpus": ".HostConfig.NanoCpus",
              "image": ".Config.Image", "network_mode": ".HostConfig.NetworkMode",
              "ports": ".NetworkSettings.Ports",
              "mounts": ".Mounts"}
    template = "{" + ",".join(json.dumps(k) + ":{{json " + v + "}}"
                              for k, v in fields.items()) + "}"
    info = json.loads(command(["docker", "inspect", "--format", template, name]))
    roots = []
    mounts = info.pop("mounts")
    for mount in mounts:
        source = Path(mount["Source"])
        if source.is_absolute() and source.is_relative_to(STATE_ROOT):
            try:
                # Ancestor symlinks must not broaden the observed tree.
                if not source.is_symlink() and source.resolve().is_relative_to(STATE_ROOT.resolve()):
                    roots.append(metadata_tree(source))
            except (OSError, ValueError) as error:
                roots.append({"complete": False, "observation_error": type(error).__name__})
    info["state_mount_metadata"] = roots
    info["excluded_mount_count"] = len(mounts) - len(roots)
    info["coverage_incomplete"] = bool(info["excluded_mount_count"] or
                                       any(not x["complete"] for x in roots))
    info["published_ports"] = [
        {"container_port": port, "host_port": item["HostPort"],
         "binding_class": binding_class(item["HostIp"])}
        for port, items in (info.pop("ports") or {}).items() for item in items or []]
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, choices=range(1, 13), default=6)
    parser.add_argument("--interval", type=int, choices=range(1, 11), default=10)
    parser.add_argument("--container-metadata", action="store_true",
                        help="Run fixed du/awk metadata helpers in the existing container after sampling")
    args = parser.parse_args()
    report = {"scope": "read-only bounded IRC capacity metadata",
              "observed_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "application_state_opened": False, "service_changed": False,
              "samples_requested": args.samples, "interval_seconds": args.interval,
              "containers": {}}
    for name in CONTAINERS:
        try:
            info = inspect(name)
            pid = info.pop("pid")
            info["samples"] = []
            report["containers"][name] = info
            if info["running"]:
                docker_stats = json.loads(command([
                    "docker", "stats", "--no-stream", "--format", "{{json .}}", name]))
                info["docker_stats_baseline"] = {key: docker_stats.get(key)
                    for key in ("CPUPerc", "MemUsage", "MemPerc", "NetIO", "BlockIO", "PIDs")}
                for i in range(args.samples):
                    generation = json.loads(command([
                        "docker", "inspect", "--format",
                        '{"pid":{{json .State.Pid}},"start":{{json .State.StartedAt}}}', name]))
                    if generation != {"pid": pid, "start": info["started_at"]}:
                        raise RuntimeError("container_generation_changed")
                    try:
                        item = sample(pid)
                    except OSError:
                        stats = json.loads(command([
                            "docker", "stats", "--no-stream", "--format", "{{json .}}", name]))
                        item = {"monotonic_seconds": time.monotonic(), "cgroup_available": False,
                                "docker_stats": {key: stats.get(key) for key in
                                    ("CPUPerc", "MemUsage", "MemPerc", "NetIO", "BlockIO", "PIDs")}}
                    info["samples"].append(item)
                    if i + 1 < args.samples:
                        time.sleep(args.interval)
                if args.container_metadata and name == "rbx-ergo":
                    helper = ("du -sk /ircd; "
                              "awk 'FNR>1 && $4==\"01\" && "
                              "($2 ~ /:1A0B$/ || $2 ~ /:1A29$/) {n++} "
                              "END {print n+0}' /proc/net/tcp /proc/net/tcp6")
                    raw = command(["docker", "exec", name, "/bin/sh", "-c", helper]).splitlines()
                    if len(raw) != 2 or raw[0].split()[-1] != "/ircd":
                        raise ValueError("unexpected_metadata_helper_output")
                    info["container_helper_metadata"] = {
                        "allocated_kib_ircd": int(raw[0].split()[0]),
                        "established_irc_tcp_connections": int(raw[1]),
                        "helper_after_samples": True, "payload_opened": False}
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
            report["containers"].setdefault(name, {})["observation_error"] = type(error).__name__
    try:
        report["same_host_backup_metadata"] = metadata_tree(STATE_ROOT / "backups")
    except (OSError, ValueError) as error:
        report["same_host_backup_metadata"] = {"complete": False,
                                              "observation_error": type(error).__name__}
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
