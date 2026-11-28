#!/usr/bin/env python3
"""Compare the reports of scripts/scan-repository.sh with its reviewed baseline.

The baseline (scripts/scan-repository-baseline.toml) names single findings,
not checks or files:

- a Trivy finding is a check id, the file it is in and the message Trivy
  prints for it, which names the resource; a finding that is listed once
  covers one occurrence;
- an osv-scanner finding is an advisory id, the lockfile and the package at
  its version.

Every entry has a date. From 00:00 UTC of that date the entry covers nothing.

The run fails when a report holds a finding the baseline does not cover: one
that is not listed, one more occurrence than listed, or one whose entry is
past its date. A listed finding that the reports no longer hold is printed as
a note and does not fail the run, so that a change which fixes or removes a
file does not fail for the list it did not edit; the entry then still covers
that same finding if it comes back before its date, and should be removed.

Usage:
  scan-repository-baseline.py --baseline FILE --trivy REPORT --osv REPORT \\
      --osv-root DIR [--today YYYY-MM-DD] [--print-findings]

Standard library only, Python 3.11 or later. Exit status: 0 when every
finding is covered, 1 when one is not, 2 when the baseline or a report cannot
be read as what it should be.
"""
from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime
import json
import sys
import tomllib

ENTRY_KEYS = {"id", "expires", "statement", "findings"}


class InputError(Exception):
    """The baseline or a report is not what this check can read."""


@dataclasses.dataclass(frozen=True)
class Entry:
    """One baseline entry: the findings of one id, with a date and a reason."""

    tool: str
    id: str
    expires: datetime.date
    statement: str
    findings: collections.Counter  # (file, text) -> occurrences


@dataclasses.dataclass
class Outcome:
    """What the comparison of one tool's report with its entries found."""

    reported: int = 0
    new: list = dataclasses.field(default_factory=list)
    past: list = dataclasses.field(default_factory=list)
    stale: list = dataclasses.field(default_factory=list)


def read_baseline(path: str) -> dict[str, list[Entry]]:
    """Read and validate the baseline; unknown keys and wrong types are errors."""
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise InputError(f"baseline {path}: {error}") from error
    unknown = set(data) - {"trivy", "osv"}
    if unknown:
        raise InputError(f"baseline {path}: unknown tables {sorted(unknown)}")
    entries: dict[str, list[Entry]] = {"trivy": [], "osv": []}
    for tool in entries:
        tables = data.get(tool, [])
        if not isinstance(tables, list):
            raise InputError(f"baseline {path}: {tool} is not an array of tables")
        for number, table in enumerate(tables, start=1):
            where = f"baseline {path}: {tool} entry {number}"
            if not isinstance(table, dict) or set(table) != ENTRY_KEYS:
                raise InputError(f"{where}: needs exactly the keys {sorted(ENTRY_KEYS)}")
            identifier, expires = table["id"], table["expires"]
            statement, findings = table["statement"], table["findings"]
            if not isinstance(identifier, str) or not identifier:
                raise InputError(f"{where}: id is not a non-empty string")
            if isinstance(expires, datetime.datetime) or not isinstance(expires, datetime.date):
                raise InputError(f"{where}: expires is not a date (YYYY-MM-DD)")
            if not isinstance(statement, str) or not statement.strip():
                raise InputError(f"{where}: statement is not a non-empty string")
            if not isinstance(findings, dict) or not findings:
                raise InputError(f"{where}: findings is not a non-empty table")
            counted: collections.Counter = collections.Counter()
            for file, texts in findings.items():
                if (not isinstance(texts, list) or not texts
                        or not all(isinstance(text, str) and text for text in texts)):
                    raise InputError(f"{where}: findings of {file!r} is not a non-empty list of strings")
                for text in texts:
                    counted[(file, text)] += 1
            entries[tool].append(Entry(tool, identifier, expires, statement, counted))
    return entries


def read_json(path: str, what: str) -> dict:
    """Read a report that has to be a JSON object."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as error:
        raise InputError(f"{what} report {path}: {error}") from error
    if not isinstance(data, dict):
        raise InputError(f"{what} report {path}: not a JSON object")
    return data


def read_trivy(path: str) -> list[dict]:
    """The findings of `trivy config --format json`: every result that is not a pass."""
    data = read_json(path, "trivy")
    if "SchemaVersion" not in data:
        raise InputError(f"trivy report {path}: no SchemaVersion, not a Trivy report")
    findings = []
    try:
        for result in data.get("Results") or []:
            for item in result.get("Misconfigurations") or []:
                if item.get("Status") == "PASS":
                    continue
                findings.append({
                    "ids": [item["ID"]],
                    "file": result["Target"],
                    "text": item["Message"],
                    "severity": item.get("Severity", "UNKNOWN"),
                })
    except (AttributeError, KeyError, TypeError) as error:
        raise InputError(f"trivy report {path}: unexpected structure: {error!r}") from error
    for finding in findings:
        if not all(isinstance(finding[key], str) and finding[key] for key in ("file", "text")) \
                or not isinstance(finding["ids"][0], str) or not finding["ids"][0]:
            raise InputError(f"trivy report {path}: a finding without id, file or message")
    return findings


def read_osv(path: str, root: str) -> list[dict]:
    """The findings of `osv-scanner scan source --format json`: one per advisory group."""
    data = read_json(path, "osv-scanner")
    if not isinstance(data.get("results"), list):
        raise InputError(f"osv-scanner report {path}: no results list, not an osv-scanner report")
    prefix = root.rstrip("/") + "/"
    findings = []
    try:
        for result in data["results"]:
            source = result["source"]["path"]
            if not source.startswith(prefix):
                raise InputError(f"osv-scanner report {path}: {source!r} is not under {root!r}")
            for package in result.get("packages") or []:
                name = package["package"]["name"]
                version = package["package"]["version"]
                groups = package.get("groups") or []
                if not groups and package.get("vulnerabilities"):
                    raise InputError(f"osv-scanner report {path}: vulnerabilities without groups")
                for group in groups:
                    ids = list(group["ids"])
                    ids += [alias for alias in group.get("aliases") or [] if alias not in ids]
                    if not ids or not all(isinstance(one, str) and one for one in ids):
                        raise InputError(f"osv-scanner report {path}: a group without ids")
                    findings.append({
                        "ids": ids,
                        "file": source[len(prefix):],
                        "text": f"{name}@{version}",
                        "severity": group.get("max_severity") or "no severity in OSV",
                    })
    except (AttributeError, KeyError, TypeError) as error:
        raise InputError(f"osv-scanner report {path}: unexpected structure: {error!r}") from error
    return findings


def compare(findings: list[dict], entries: list[Entry], today: datetime.date) -> Outcome:
    """Match every reported finding with one listed occurrence, if there is one."""
    active: collections.Counter = collections.Counter()
    lapsed: collections.Counter = collections.Counter()
    dates: dict = {}
    for entry in entries:
        target = active if today < entry.expires else lapsed
        for (file, text), count in entry.findings.items():
            key = (entry.id, file, text)
            target[key] += count
            if target is lapsed:
                dates[key] = max(dates.get(key, entry.expires), entry.expires)
    outcome = Outcome(reported=len(findings))
    for finding in findings:
        keys = [(one, finding["file"], finding["text"]) for one in finding["ids"]]
        covered = next((key for key in keys if active[key] > 0), None)
        if covered is not None:
            active[covered] -= 1
            continue
        expired = next((key for key in keys if lapsed[key] > 0), None)
        if expired is not None:
            lapsed[expired] -= 1
            outcome.past.append((finding, dates[expired]))
        else:
            outcome.new.append(finding)
    for left in (active, lapsed):
        for (identifier, file, text), count in sorted(left.items()):
            if count > 0:
                outcome.stale.append((identifier, file, text, count))
    return outcome


def describe(finding: dict) -> str:
    return (f"{'/'.join(finding['ids'])} ({finding['severity']}) "
            f"{finding['file']}: {finding['text']}")


def report(tool: str, outcome: Outcome) -> bool:
    """Print the outcome for one tool; true when every finding is covered."""
    covered = outcome.reported - len(outcome.new) - len(outcome.past)
    print(f"{tool}: {outcome.reported} reported, {covered} covered by the baseline, "
          f"{len(outcome.new)} not listed, {len(outcome.past)} past their date")
    for finding in outcome.new:
        print(f"  NOT LISTED  {describe(finding)}")
    for finding, date in outcome.past:
        print(f"  PAST {date}  {describe(finding)}")
    for identifier, file, text, count in outcome.stale:
        print(f"  note: listed and no longer reported, remove from the baseline "
              f"({count}x): {identifier} {file}: {text}")
    return not outcome.new and not outcome.past


def print_findings(tool: str, findings: list[dict]) -> None:
    """Print the reported findings as the findings tables of baseline entries."""
    by_id: dict = collections.defaultdict(lambda: collections.defaultdict(list))
    for finding in findings:
        by_id[finding["ids"][0]][finding["file"]].append(finding["text"])
    for identifier in sorted(by_id):
        files = by_id[identifier]
        total = sum(len(texts) for texts in files.values())
        print(f"# {tool} {identifier}: {total} in {len(files)} file(s)")
        print(f"[{tool}.findings]")
        for file in sorted(files):
            print(f"{json.dumps(file)} = [")
            for text in sorted(files[file]):
                print(f"  {json.dumps(text)},")
            print("]")
        print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--trivy", required=True, help="report of trivy config --format json")
    parser.add_argument("--osv", required=True, help="report of osv-scanner --format json")
    parser.add_argument("--osv-root", required=True,
                        help="directory osv-scanner scanned, as it appears in its report")
    parser.add_argument("--today", type=datetime.date.fromisoformat,
                        default=datetime.datetime.now(datetime.timezone.utc).date(),
                        help="date to judge the entries by (default: today, UTC)")
    parser.add_argument("--print-findings", action="store_true",
                        help="print the reported findings as baseline tables and exit")
    arguments = parser.parse_args(argv)
    try:
        reports = {"trivy": read_trivy(arguments.trivy),
                   "osv": read_osv(arguments.osv, arguments.osv_root)}
        if arguments.print_findings:
            for tool, findings in reports.items():
                print_findings(tool, findings)
            return 0
        entries = read_baseline(arguments.baseline)
    except InputError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"baseline {arguments.baseline}, judged on {arguments.today} (UTC)")
    results = [report(tool, compare(reports[tool], entries[tool], arguments.today))
               for tool in ("trivy", "osv")]
    if all(results):
        return 0
    print("A finding is not covered. Fix it, or add it to the baseline as a reviewed "
          "entry with a date and a reason.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
