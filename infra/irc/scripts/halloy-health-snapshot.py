#!/usr/bin/env python3
"""Read-only Linux Halloy metrics; hash config bytes without emitting values.

Chat content is not read. Config bytes are read only to compute SHA256; the
configuration may contain inline credentials, so it must never be printed.
"""

import argparse
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import time
from datetime import datetime, timezone


TCP_STATES = {
    "01": "ESTABLISHED", "02": "SYN_SENT", "03": "SYN_RECV",
    "04": "FIN_WAIT1", "05": "FIN_WAIT2", "06": "TIME_WAIT",
    "07": "CLOSE", "08": "CLOSE_WAIT", "09": "LAST_ACK",
    "0A": "LISTEN", "0B": "CLOSING", "0C": "NEW_SYN_RECV",
}


def timestamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def failure(errors, source, exc):
    # Exception text can contain paths or input; emit only a fixed source/code.
    code = errno.errorcode.get(getattr(exc, "errno", None), type(exc).__name__)
    errors.append({"source": source, "code": code})


def cpu_sample(pid):
    raw = (Path("/proc") / str(pid) / "stat").read_text()
    fields = raw[raw.rfind(")") + 2:].split()
    return int(fields[19]), int(fields[11]) + int(fields[12]), time.monotonic()


def discover(errors):
    pids = []
    unreadable = 0
    try:
        entries = list(Path("/proc").iterdir())
    except OSError as exc:
        failure(errors, "process_discovery", exc)
        return pids
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            if (entry / "comm").read_bytes().rstrip(b"\n") == b"halloy":
                pids.append(int(entry.name))
        except FileNotFoundError:
            pass  # Processes commonly exit during discovery.
        except OSError:
            unreadable += 1
    if unreadable:
        errors.append({"source": "process_discovery", "unreadable_count": unreadable})
    return sorted(pids)


def process_snapshot(pid, before, ticks_per_second, boot_uptime):
    result = {"pid": pid, "errors": []}
    errors = result["errors"]
    root = Path("/proc") / str(pid)
    try:
        if (root / "comm").read_bytes().rstrip(b"\n") != b"halloy":
            result["errors"].append({"source": "process_identity", "code": "CHANGED"})
            return result
        after = cpu_sample(pid)
        if before is not None and before[0] != after[0]:
            result["errors"].append({"source": "process_identity", "code": "PID_REUSED"})
            return result
        if boot_uptime is not None:
            result["uptime_seconds"] = round(max(0, boot_uptime - after[0] / ticks_per_second), 2)
        if before is not None:
            elapsed = after[2] - before[2]
            result["cpu_sample_seconds"] = round(elapsed, 3)
            result["cpu_percent_one_core"] = round(
                (after[1] - before[1]) / ticks_per_second / elapsed * 100, 3
            )
    except (OSError, ValueError, IndexError) as exc:
        failure(errors, "process_stat", exc)
        return result
    for filename, fields in (
        ("status", {"VmRSS": "rss_kib", "VmHWM": "peak_rss_kib", "VmSwap": "swap_kib", "Threads": "threads"}),
        ("smaps_rollup", {"Pss": "pss_kib"}),
    ):
        try:
            for line in (root / filename).read_text().splitlines():
                key, _, value = line.partition(":")
                if key in fields:
                    result[fields[key]] = int(value.split()[0])
        except (OSError, ValueError, IndexError) as exc:
            failure(errors, filename, exc)
    try:
        descriptors = list((root / "fd").iterdir())
        result["fd_count"] = len(descriptors)
        inodes = set()
        unreadable = 0
        for descriptor in descriptors:
            try:
                target = os.readlink(descriptor)
                if target.startswith("socket:[") and target.endswith("]"):
                    inodes.add(target[8:-1])
            except OSError:
                unreadable += 1
        if unreadable:
            errors.append({"source": "socket_correlation", "unreadable_fd_count": unreadable})
        sockets = []
        # IPv4 only: retain only this process's socket inodes and 127/8 peers.
        for line in (root / "net/tcp").read_text().splitlines()[1:]:
            columns = line.split()
            if len(columns) < 10 or columns[9] not in inodes:
                continue
            local_address, _ = columns[1].split(":")
            peer_address, peer_port = columns[2].split(":")
            loopback_suffix = "7F" if os.sys.byteorder == "little" else None
            local_loopback = local_address.endswith(loopback_suffix) if loopback_suffix else local_address.startswith("7F")
            peer_loopback = peer_address.endswith(loopback_suffix) if loopback_suffix else peer_address.startswith("7F")
            if not local_loopback or not peer_loopback or int(peer_port, 16) != 6667:
                continue
            tx_queue, rx_queue = columns[4].split(":")
            sockets.append({
                "family": "ipv4", "peer_port": 6667,
                "state": TCP_STATES.get(columns[3], "UNKNOWN"),
                "send_queue_bytes": int(tx_queue, 16),
                "receive_queue_bytes": int(rx_queue, 16),
            })
        result["local_irc_sockets"] = sockets
    except (OSError, ValueError, IndexError) as exc:
        failure(errors, "file_descriptors_or_tcp_metadata", exc)
    return result


def persistence_metadata(path):
    result = {"file_count": 0, "total_bytes": 0, "newest_mtime_utc": None, "symlinks_skipped": 0, "errors": []}
    newest = None
    pending = [path]
    while pending:
        current = pending.pop()
        try:
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode):
                result["symlinks_skipped"] += 1
            elif stat.S_ISDIR(info.st_mode):
                pending.extend(current.iterdir())
            elif stat.S_ISREG(info.st_mode):
                result["file_count"] += 1
                result["total_bytes"] += info.st_size
                newest = info.st_mtime if newest is None else max(newest, info.st_mtime)
        except OSError as exc:
            failure(result["errors"], "persistence_metadata", exc)
    if newest is not None:
        result["newest_mtime_utc"] = timestamp(newest)
    return result


def interval_argument(value):
    interval = float(value)
    if not math.isfinite(interval) or not 0 < interval <= 10:
        raise argparse.ArgumentTypeError("interval must be greater than 0 and at most 10 seconds")
    return interval


def main():
    parser = argparse.ArgumentParser(
        description="Captura única somente leitura: métricas Halloy, SHA256 da configuração e metadados de persistência.",
        epilog="JSON sanitizado em stdout. Para salvar: umask 077; python3 halloy-health-snapshot.py > snapshot.json. Não captura mensagens nem valores de configuração.",
    )
    parser.add_argument("--interval", type=interval_argument, default=2.0, metavar="SECONDS", help="intervalo da amostra de CPU (padrão: 2; máximo: 10)")
    args = parser.parse_args()
    result = {"schema_version": 1, "started_at_utc": timestamp(time.time()), "requested_interval_seconds": args.interval, "path_scope": "current_user_xdg_or_linux_defaults", "errors": []}
    errors = result["errors"]
    pids = discover(errors)
    before = {}
    for pid in pids:
        try:
            before[pid] = cpu_sample(pid)
        except (OSError, ValueError, IndexError) as exc:
            failure(errors, "initial_process_stat", exc)
    if pids:
        time.sleep(args.interval)
    try:
        uptime = float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError) as exc:
        failure(errors, "system_uptime", exc)
        uptime = None
    ticks_per_second = os.sysconf("SC_CLK_TCK")
    result["processes"] = [process_snapshot(pid, before.get(pid), ticks_per_second, uptime) for pid in pids]
    home = Path.home()
    config_dir = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "halloy"
    data_dir = Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share") / "halloy"
    try:
        digest = hashlib.sha256()
        with (config_dir / "config.toml").open("rb") as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                digest.update(chunk)
        result["config_sha256"] = digest.hexdigest()
    except OSError as exc:
        failure(errors, "config_sha256", exc)
    result["persistence"] = {
        name: persistence_metadata(data_dir / name)
        for name in ("history", "logs", "dashboard.json.gz", "drafts.json", "window.json", "targets")
    }
    result["finished_at_utc"] = timestamp(time.time())
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()
