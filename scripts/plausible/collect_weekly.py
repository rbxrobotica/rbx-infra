#!/usr/bin/env python3
"""Collect aggregate-only Plausible evidence and write a local weekly snapshot.

One bounded SELECT per run: no raw identifiers, paths, query strings or writes.
Unknown deployment dates remain unknown, even when the query succeeds. Pass the
deployment timestamp for each verified site; omitted sites are never reported as
zero. For the initially verified Swiss release, use:

  python3 scripts/plausible/collect_weekly.py --kubeconfig /safe/kubeconfig \
    --instrumented-since rbxsystems.ch=2026-10-05T18:34:48Z \
    --output-dir /safe/weekly-reports

--as-of defaults to the current UTC minute. Optional --backup-receipts reads a
JSON list conforming to weekly_report.clean_receipts. Each invocation preserves
aggregate input, report JSON and reviewable Markdown under a content-id suffix.
A query/parse failure still writes an unknown report and exits 2. No credentials,
raw query output or error text are included in that report. No upload or Flight
Deck Signal is performed. The caller owns retention and backup of these files.
"""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import subprocess
import sys

try:
    from . import weekly_report as report
except ImportError:
    # Direct invocation, including offline tests loading this file by path.
    import importlib.util
    _spec = importlib.util.spec_from_file_location("weekly_report", Path(__file__).with_name("weekly_report.py"))
    report = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(report)


SITE_IDS = {"rbx.ia.br": 1, "rbxsystems.ch": 2}
QUERY_VERSION = "partnership-minute-aggregate-v1"
OFFER_VERSION = "2026-10-05"
NAMESPACE = "plausible"
POD = "plausible-clickhouse-0"
DIMENSIONS = ("locale", "surface", "entry", "destination", "product", "error")
PARAMETER_NAMES = frozenset({"as_of_epoch", "window_start_epoch", "site_1_start_epoch", "site_2_start_epoch",
                             "site_1_enabled", "site_2_enabled", "offer", "offer_version"})


class CollectionError(Exception):
    """An intentionally generic failure that carries no query or credential text."""


def parse_instrumented(values):
    result = {}
    for value in values:
        site, separator, instant = value.partition("=")
        if not separator or site not in SITE_IDS or site in result:
            raise ValueError("instrumented-since must name each supported site at most once")
        parsed = report.timestamp(instant, "instrumented_since")
        if parsed.microsecond:
            raise ValueError("instrumentation timestamps require whole-second precision")
        result[site] = parsed
    return result


def utc_minute(value):
    if value.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    value = value.astimezone(timezone.utc)
    if value.second or value.microsecond:
        raise ValueError("as_of must align to a whole minute")
    return value


def sql_list(values):
    # All values originate in the renderer's fixed vocabularies.
    if any(not re.fullmatch(r"[a-zA-Z0-9._-]+", value) for value in values):
        raise ValueError("SQL vocabulary contains an unsupported token")
    return "(" + ", ".join("'" + value + "'" for value in sorted(values)) + ")"


def build_parameters(as_of, instrumented):
    """Values cross the SQL boundary only as explicitly typed ClickHouse parameters."""
    as_of = utc_minute(as_of)
    start = report.windows(as_of)["last_complete"][0].astimezone(timezone.utc)
    if any(site not in SITE_IDS or not isinstance(at, datetime) or at.tzinfo is None or at.microsecond
           for site, at in instrumented.items()):
        raise ValueError("invalid instrumentation scope")

    def epoch(value):
        result = int(value.timestamp())
        if not 0 <= result <= 2**32 - 1:
            raise ValueError("timestamp is outside the ClickHouse DateTime range")
        return str(result)

    parameters = {"as_of_epoch": epoch(as_of), "window_start_epoch": epoch(start),
                  "offer": report.OFFER, "offer_version": OFFER_VERSION}
    for site, site_id in SITE_IDS.items():
        since = instrumented.get(site)
        known = since is not None and since < as_of
        parameters[f"site_{site_id}_enabled"] = "1" if known else "0"
        parameters[f"site_{site_id}_start_epoch"] = epoch(max(start, since)) if known else epoch(start)
    return parameters


def build_query():
    """Build structure from fixed identifiers/vocabularies, never from CLI values."""
    aliases = [f"arrayElement(`meta.value`, indexOf(`meta.key`, '{field}')) AS {field}_value"
               for field in ("offer", "offer_version", *DIMENSIONS)]
    # Start the first deployment bucket at the actual timestamp, not before it.
    starts = "if(site_id = 1, toDateTime({site_1_start_epoch:UInt32}, 'UTC'), toDateTime({site_2_start_epoch:UInt32}, 'UTC'))"
    aliases.append(f"greatest(toStartOfMinute(timestamp), {starts}) AS bucket_at")
    selects = ["if(site_id = 1, 'rbx.ia.br', 'rbxsystems.ch') AS site", "name AS event",
               "formatDateTime(bucket_at, '%Y-%m-%dT%H:%i:%SZ', 'UTC') AS bucket_start",
               "dateDiff('second', bucket_at, toStartOfMinute(bucket_at) + INTERVAL 1 MINUTE) AS bucket_seconds",
               "count() AS count", "formatDateTime(max(timestamp), '%Y-%m-%dT%H:%i:%SZ', 'UTC') AS watermark"]
    selects += [f"if({field}_value IN {sql_list(allowed)}, {field}_value, '') AS {field}"
                for field in DIMENSIONS for allowed in (report.VOCABULARIES[field],)]
    required = "\n  AND ".join(f"{field}_value IN {sql_list(report.VOCABULARIES[field])}" for field in report.REQUIRED_PROPERTIES)
    # Only fixed DIMENSIONS and validated fixed vocabulary tokens form clauses.
    # All externally supplied dates are typed parameters, passed separately in argv.
    clauses = [
        "WITH", ",\n  ".join(aliases), "SELECT", ",\n  ".join(selects),
        "FROM plausible_events.events_v2",
        "WHERE timestamp >= toDateTime({window_start_epoch:UInt32}, 'UTC')",
        "  AND timestamp < toDateTime({as_of_epoch:UInt32}, 'UTC')",
        "  AND ((site_id = 1 AND {site_1_enabled:UInt8} = 1 AND timestamp >= toDateTime({site_1_start_epoch:UInt32}, 'UTC'))",
        " OR (site_id = 2 AND {site_2_enabled:UInt8} = 1 AND timestamp >= toDateTime({site_2_start_epoch:UInt32}, 'UTC')))",
        "  AND name IN " + sql_list(report.EVENTS),
        "  AND offer_value = {offer:String}", "  AND offer_version_value = {offer_version:String}",
        "  AND " + required,
        "GROUP BY site_id, name, bucket_at, " + ", ".join(DIMENSIONS),
        "ORDER BY site, bucket_at, event, " + ", ".join(DIMENSIONS),
        "SETTINGS max_execution_time=20, max_memory_usage=268435456, max_result_bytes=33554432, "
        "max_result_rows=250000, result_overflow_mode='throw', timeout_overflow_mode='throw', max_threads=2",
        "FORMAT JSONEachRow",
    ]
    return "\n".join(clauses)


def run_query(query, kubeconfig, parameters):
    if set(parameters) != PARAMETER_NAMES:
        raise ValueError("query requires the exact supported parameter names")
    arguments = [f"--param_{name}={value}" for name, value in sorted(parameters.items())]
    try:
        result = subprocess.run(
            ["kubectl", "--kubeconfig", str(kubeconfig), "--request-timeout=30s", "-n", NAMESPACE,
             "exec", POD, "--", "clickhouse-client", *arguments, "--query", query],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=35, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CollectionError("query unavailable") from exc
    if result.returncode or len(result.stdout) > report.MAX_BYTES:
        raise CollectionError("query unavailable or result exceeds bounds")
    return result.stdout


def parse_result(raw, instrumented, as_of):
    if not isinstance(raw, bytes) or len(raw) > report.MAX_BYTES:
        raise CollectionError("invalid aggregate result")
    events, watermarks = [], {}
    try:
        for line in raw.decode("utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or row.get("site") not in instrumented:
                raise ValueError("unexpected site")
            if row.get("event") not in report.EVENTS:
                raise ValueError("unexpected event")
            count = row.get("count")
            if isinstance(count, str) and re.fullmatch(r"[0-9]{1,19}", count):
                count = int(count)
            width = row.get("bucket_seconds")
            if isinstance(width, str) and re.fullmatch(r"[0-9]{1,2}", width):
                width = int(width)
            properties = {"offer": report.OFFER, "offer_version": OFFER_VERSION}
            properties.update({field: row[field] for field, allowed in report.VOCABULARIES.items()
                               if isinstance(row.get(field), str) and row[field] in allowed})
            item = {"site": row["site"], "event": row["event"], "count": count,
                    "bucket_start": row["bucket_start"], "bucket_seconds": width,
                    "properties": properties}
            begin = report.timestamp(item["bucket_start"], "bucket_start")
            if type(width) is not int or not 1 <= width <= 60:
                raise ValueError("invalid minute bucket")
            end = begin + timedelta(seconds=width)
            mark = report.timestamp(row["watermark"], "watermark")
            lower = max(instrumented[row["site"]], report.windows(as_of)["last_complete"][0])
            if begin < lower or end > as_of or not begin <= mark < end:
                raise ValueError("aggregate is outside declared coverage")
            watermarks[row["site"]] = max(mark, watermarks.get(row["site"], mark))
            events.append(item)
            if len(events) > report.MAX_ROWS:
                raise ValueError("too many aggregate rows")
        _, discarded = report.clean_rows(events, list(SITE_IDS), OFFER_VERSION)
        if discarded:
            raise ValueError("aggregate result violated the event contract")
    except (KeyError, TypeError, UnicodeError, ValueError) as exc:
        raise CollectionError("invalid aggregate result") from exc
    return events, watermarks


def collect(as_of, instrumented, kubeconfig, runner=run_query, clock=None, backup_receipts=None):
    as_of = utc_minute(as_of)
    clock = clock or (lambda: datetime.now(timezone.utc))
    if as_of > clock():
        raise ValueError("as_of cannot be in the future")
    if any(site not in SITE_IDS or at.tzinfo is None or at.microsecond for site, at in instrumented.items()):
        raise ValueError("invalid instrumentation scope")
    query = build_query()
    parameters = build_parameters(as_of, instrumented)
    start = report.windows(as_of)["last_complete"][0].astimezone(timezone.utc)
    events, watermarks, status = [], {}, "ok"
    try:
        events, watermarks = parse_result(runner(query, kubeconfig, parameters), instrumented, as_of)
    except CollectionError:
        status = "error"
    coverage = {}
    for site in SITE_IDS:
        since = instrumented.get(site)
        known = since is not None and since < as_of and status == "ok"
        coverage[site] = {"status": "ok" if known else "unknown",
                          "instrumented_since": report.iso(since) if since is not None else None,
                          "coverage_start": report.iso(max(start, since)) if known else None,
                          "coverage_end": report.iso(as_of) if known else None,
                          "watermark": report.iso(watermarks[site]) if site in watermarks else None}
    data = {"schema_version": "plausible-weekly-input-v1", "as_of": report.iso(as_of),
            "offer_version": OFFER_VERSION, "sites": sorted(SITE_IDS),
            "source": {"system": "plausible-clickhouse", "query_version": QUERY_VERSION,
                       "status": status, "extracted_at": report.iso(clock()),
                       "coverage_start": report.iso(start), "coverage_end": report.iso(as_of),
                       "watermark": report.iso(max(watermarks.values())) if watermarks else None,
                       "site_coverage": coverage},
            "events": events, "backup_receipts": backup_receipts or []}
    # Validate before writing a snapshot; no external free text can enter it.
    report.build_report(data)
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kubeconfig", required=True, type=Path)
    parser.add_argument("--instrumented-since", action="append", default=[], metavar="SITE=ISO_TIMESTAMP")
    parser.add_argument("--as-of", help="UTC-minute-aligned timestamp; default is the current UTC minute")
    parser.add_argument("--backup-receipts", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        as_of = report.timestamp(args.as_of, "as_of") if args.as_of else now
        receipts = json.loads(args.backup_receipts.read_bytes()) if args.backup_receipts else []
        receipts = report.clean_receipts(receipts)
        data = collect(as_of, parse_instrumented(args.instrumented_since), args.kubeconfig, backup_receipts=receipts)
        rendered = report.build_report(data)
        prefix = f"plausible-weekly-{as_of.strftime('%Y%m%dT%H%M%SZ')}-{rendered['report_id'][:12]}"
        paths = {"input": args.output_dir / (prefix + ".input.json"),
                 "report": args.output_dir / (prefix + ".json"),
                 "markdown": args.output_dir / (prefix + ".md")}
        report.atomic_write(paths["input"], json.dumps(data, indent=2, sort_keys=True) + "\n")
        report.atomic_write(paths["report"], json.dumps(rendered, indent=2, sort_keys=True) + "\n")
        report.atomic_write(paths["markdown"], report.render_markdown(rendered))
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"Weekly collection failed: {type(exc).__name__}; check configuration and output accessibility.", file=sys.stderr)
        return 1
    print(json.dumps({**{key: str(path) for key, path in paths.items()},
                      "report_id": rendered["report_id"], "source_status": data["source"]["status"]}))
    return 0 if data["source"]["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
