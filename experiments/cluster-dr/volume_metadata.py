#!/usr/bin/env python3
"""Metadata-only allocated usage for explicitly supplied existing local PV roots."""

import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys


ROOT = Path("/var/lib/rancher/k3s/storage")


def measure(item):
    path = Path(item["path"])
    result = {k: item[k] for k in ("pv", "namespace", "claim", "node")}
    result["source_path_sha256"] = hashlib.sha256(str(path).encode()).hexdigest()
    if (path.parent != ROOT or not re.fullmatch(r"pvc-[0-9a-f-]{36}_[a-z0-9.-]+_[a-z0-9.-]+", path.name)
            or ROOT.resolve() != ROOT):
        return {**result, "gap": "unsupported_volume_path"}
    try:
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or path.resolve() != path:
            return {**result, "gap": "not_an_existing_nonsymlink_directory"}
        args = ["du", "-s", "-x", "-B1", "--", str(path)]
        if shutil.which("nice"):
            args = ["nice", "-n", "19", *args]
        if shutil.which("ionice"):
            args = ["ionice", "-c", "3", *args]
        proc = subprocess.run(args, capture_output=True, text=True, timeout=15)
        if proc.returncode or len(proc.stdout) > 8192:
            return {**result, "gap": "du_unavailable_or_incomplete"}
        value, observed_path = proc.stdout.strip().split("\t", 1)
        if observed_path != str(path) or not value.isdigit():
            return {**result, "gap": "du_output_mismatch"}
        fs = os.statvfs(path)
        result.update({"allocated_bytes": int(value),
                       "backing_filesystem_capacity_bytes": fs.f_blocks * fs.f_frsize,
                       "backing_filesystem_available_bytes": fs.f_bavail * fs.f_frsize,
                       "observation_not_transactionally_consistent": True})
        return result
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        return {**result, "gap": type(error).__name__}


def main():
    raw = sys.stdin.read(65537)
    if len(raw) > 65536:
        raise ValueError("input_bound")
    items = json.loads(raw)
    if not isinstance(items, list) or len(items) > 32:
        raise ValueError("volume_count_bound")
    return {"type": "read-only allocated local-PV metadata",
            "observed_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "payloads_opened": False, "state_changed": False,
            "volumes": [measure(item) for item in items]}


if __name__ == "__main__":
    print(json.dumps(main(), sort_keys=True))
