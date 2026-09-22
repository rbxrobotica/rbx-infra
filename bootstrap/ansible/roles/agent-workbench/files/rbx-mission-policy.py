#!/usr/bin/env python3
"""Evaluate the repository-bound path and diff policy for a staged worktree."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


def glob_regex(pattern: str) -> re.Pattern[str]:
    out: list[str] = ["^"]
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                i += 2
                if i < len(pattern) and pattern[i] == "/":
                    out.append("(?:.*/)?")
                    i += 1
                else:
                    out.append(".*")
            else:
                out.append("[^/]*")
                i += 1
        elif char == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(char))
            i += 1
    out.append("$")
    return re.compile("".join(out))


def matches(path: str, patterns: list[str]) -> bool:
    return any(glob_regex(pattern).match(path) for pattern in patterns)


def git(worktree: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(worktree), *args])


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: rbx-mission-policy.py CONTRACT_JSON WORKTREE OUTPUT_JSON", file=sys.stderr)
        return 64
    contract_path, worktree_path, output_path = map(Path, sys.argv[1:])
    contract = json.loads(contract_path.read_text())
    worktree = worktree_path.resolve()

    allowed = contract.get("allowed_paths")
    forbidden = contract.get("forbidden_paths")
    if not isinstance(allowed, list) or not allowed or not all(isinstance(v, str) and v for v in allowed):
        raise ValueError("contract allowed_paths must be a non-empty string array")
    if not isinstance(forbidden, list) or not all(isinstance(v, str) and v for v in forbidden):
        raise ValueError("contract forbidden_paths must be a string array")

    raw_names = git(worktree, "diff", "--cached", "--name-only", "-z")
    changed = sorted(name.decode("utf-8") for name in raw_names.split(b"\0") if name)
    violations: list[str] = []
    for name in changed:
        normalized = Path(name).as_posix()
        if normalized.startswith("/") or ".." in Path(normalized).parts or normalized != name:
            violations.append(f"{name}:invalid_repository_path")
        elif matches(name, forbidden):
            violations.append(f"{name}:forbidden_path")
        elif not matches(name, allowed):
            violations.append(f"{name}:outside_allowed_paths")

    lines = 0
    binary_files: list[str] = []
    numstat = git(worktree, "diff", "--cached", "--numstat").decode("utf-8", errors="replace")
    for row in numstat.splitlines():
        parts = row.split("\t", 2)
        if len(parts) < 3:
            continue
        added, deleted, filename = parts
        if added == "-" or deleted == "-":
            binary_files.append(filename)
        else:
            lines += int(added) + int(deleted)

    stop_reason = "forbidden_action_attempted" if violations else None
    bound = contract.get("max_diff_size")
    if isinstance(bound, str):
        match = re.fullmatch(r"([0-9]+)\s?(lines|files)", bound)
        if match:
            limit, unit = int(match.group(1)), match.group(2)
            actual = len(changed) if unit == "files" else lines
            if (unit == "lines" and binary_files) or actual > limit:
                detail = "binary_diff_unmeasurable" if binary_files and unit == "lines" else f"{actual}_{unit}_exceeds_{limit}"
                violations.append(f"diff:{detail}")
                stop_reason = "diff_size_exceeded"

    result = {
        "status": "failed" if violations else "passed",
        "violations": violations,
        "stop_reason": stop_reason,
        "changed_files": changed,
        "diff_files": len(changed),
        "diff_lines": lines,
        "binary_files": binary_files,
    }
    output_path.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return 0 if not violations else 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.CalledProcessError, ValueError, json.JSONDecodeError) as exc:
        print(f"mission policy error: {exc}", file=sys.stderr)
        raise SystemExit(2)
