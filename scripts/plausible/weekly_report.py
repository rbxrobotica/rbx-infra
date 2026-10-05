#!/usr/bin/env python3
"""Render deterministic weekly evidence from an aggregate-only Plausible export.

No database/network calls or Flight Deck mutations are made. The collector owns
one successful, bounded query and must attest its coverage, even for zero rows.
Input (all dates include a UTC offset; coverage end is exclusive)::

  {"schema_version": "plausible-weekly-input-v1",
   "as_of": "2026-10-11T18:00:00-03:00", "offer_version": "2026-10-05",
   "sites": ["rbx.ia.br", "rbxsystems.ch"],
   "source": {"system": "plausible-clickhouse", "query_version": "events-v1",
     "status": "ok", "extracted_at": "2026-10-11T21:01:00Z",
     "coverage_start": "2026-09-28T03:00:00Z",
     "coverage_end": "2026-10-11T21:00:00Z", "watermark": null},
   "events": [{"site": "rbx.ia.br", "event": "offer_view", "count": 3,
     "bucket_start": "2026-10-10T15:00:00Z", "bucket_seconds": 60,
     "properties": {"offer": "engineering-partnership",
       "offer_version": "2026-10-05", "locale": "pt-BR",
       "surface": "partnership", "entry": "offer"}}],
   "backup_receipts": [{"id": "backup-20261011", "location": "/safe/receipt.json",
     "manifest_sha256": "<64 lowercase hex characters>",
     "verified_at": "2026-10-11T20:00:00Z", "restore_tested_at": null}]}

Use minute buckets and a minute-aligned as_of. A bucket crossing a report cut
makes that site's affected period unknown: proportional counts are never guessed.
Source status=error accepts absent coverage/watermark and yields unknown counts.
Watermark is the last observed event, not proof of query freshness. Backup receipt
fields are collector attestations, not a restore performed by this renderer.
Optional source.site_coverage maps site names to {status: ok|unknown,
instrumented_since, coverage_start, coverage_end, watermark}. When this map is
present, missing/unknown sites stay unknown. A post-launch subset appears only
as observed_partial, never as a whole-week count or a comparable conversion.

Example: python3 scripts/plausible/weekly_report.py --input aggregate.json \
  --json-output weekly.json --markdown-output weekly.md
Memory is bounded by a 32 MiB input / 250,000 rows; output is bounded by fixed
event/property vocabularies. Partial coverage never becomes an observed zero.
"""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


TIMEZONE = "America/Sao_Paulo"
TZ = ZoneInfo(TIMEZONE)
OFFER = "engineering-partnership"
SITES = {"rbx.ia.br", "rbxsystems.ch"}
EVENTS = (
    "offer_view", "cta_click", "evidence_view", "evidence_code_open",
    "form_start", "form_submit", "form_success", "form_error",
)
VOCABULARIES = {
    "locale": {"pt-BR", "en"},
    "surface": {"products", "partnership"},
    "entry": {"hero", "footer", "offer", "form", "gallery", "chat"},
    "destination": {"partnership", "qualification", "portfolio", "product", "source", "capture"},
    "product": {"robson", "strategos", "verentir", "thalamus", "robson-code", "satwake", "kulinaryos"},
    "error": {"validation", "challenge", "network", "http", "timeout", "unavailable"},
}
REQUIRED_PROPERTIES = ("locale", "surface", "entry")
MAX_BYTES = 32 * 1024 * 1024
MAX_ROWS = 250_000


def iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def timestamp(value, field):
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an offset-aware ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an offset-aware ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def token(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}", value):
        raise ValueError(f"{field} must be a bounded identifier")
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def windows(as_of):
    """Monday-inclusive / Monday-exclusive local periods, with equal local cuts."""
    local = as_of.astimezone(TZ)
    start = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    previous = start - timedelta(days=7)
    return {
        "current": (start, local, False),
        "previous_comparable": (previous, local - timedelta(days=7), False),
        "last_complete": (previous, start, True),
    }


def clean_source(raw):
    if not isinstance(raw, dict) or raw.get("system") != "plausible-clickhouse":
        raise ValueError("source.system must be plausible-clickhouse")
    if raw.get("status") not in ("ok", "error"):
        raise ValueError("source.status must be ok or error")
    result = {
        "system": "plausible-clickhouse", "status": raw["status"],
        "query_version": token(raw.get("query_version"), "source.query_version"),
        "extracted_at": iso(timestamp(raw.get("extracted_at"), "source.extracted_at")),
    }
    for field in ("coverage_start", "coverage_end", "watermark"):
        value = raw.get(field)
        result[field] = iso(timestamp(value, f"source.{field}")) if value is not None else None
    if (result["watermark"] is not None
            and timestamp(result["watermark"], "watermark") > timestamp(result["extracted_at"], "extracted_at")):
        raise ValueError("source watermark cannot be later than extraction")
    if result["status"] == "ok":
        if not result["coverage_start"] or not result["coverage_end"]:
            raise ValueError("a successful source requires explicit coverage")
        start = timestamp(result["coverage_start"], "coverage_start")
        end = timestamp(result["coverage_end"], "coverage_end")
        if start >= end:
            raise ValueError("source coverage must have a positive duration")
        if end > timestamp(result["extracted_at"], "extracted_at"):
            raise ValueError("source coverage cannot extend beyond extraction")
    if "site_coverage" in raw:
        entries = raw["site_coverage"]
        if not isinstance(entries, dict) or any(site not in SITES for site in entries):
            raise ValueError("site_coverage must map supported site names")
        result["site_coverage"] = {}
        for site, entry in sorted(entries.items()):
            if not isinstance(entry, dict) or entry.get("status") not in ("ok", "unknown"):
                raise ValueError("site coverage status must be ok or unknown")
            clean = {"status": entry["status"]}
            for field in ("instrumented_since", "coverage_start", "coverage_end", "watermark"):
                clean[field] = iso(timestamp(entry[field], field)) if entry.get(field) is not None else None
            if clean["status"] == "ok":
                if not all(clean[field] for field in ("instrumented_since", "coverage_start", "coverage_end")):
                    raise ValueError("observed site coverage requires an instrumentation timestamp and bounds")
                begin = timestamp(clean["coverage_start"], "coverage_start")
                end = timestamp(clean["coverage_end"], "coverage_end")
                if (begin < timestamp(clean["instrumented_since"], "instrumented_since") or begin >= end
                        or end > timestamp(result["extracted_at"], "extracted_at")):
                    raise ValueError("site coverage must fit instrumentation and extraction bounds")
                if result["status"] == "ok" and (
                        begin < timestamp(result["coverage_start"], "coverage_start")
                        or end > timestamp(result["coverage_end"], "coverage_end")):
                    raise ValueError("site coverage cannot exceed source query coverage")
            if clean["watermark"] and timestamp(clean["watermark"], "watermark") > timestamp(result["extracted_at"], "extracted_at"):
                raise ValueError("site watermark cannot exceed extraction")
            result["site_coverage"][site] = clean
    return result


def clean_receipts(raw):
    if not isinstance(raw, list) or len(raw) > 100:
        raise ValueError("backup_receipts must be a bounded list")
    result = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("backup receipt must be an object")
        receipt_id = token(item.get("id"), "backup receipt id")
        if receipt_id in seen:
            raise ValueError("backup receipt ids must be unique")
        seen.add(receipt_id)
        digest = item.get("manifest_sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("backup manifest_sha256 must be a SHA-256 digest")
        location = item.get("location")
        if not isinstance(location, str) or not 1 <= len(location) <= 2000:
            raise ValueError("backup receipt location must be a bounded reference")
        parsed = urlsplit(location)
        if (any(ord(c) < 32 for c in location) or parsed.query or parsed.fragment
                or parsed.username or parsed.password or "<" in location or ">" in location
                or not (location.startswith("/") or (parsed.scheme in {"https", "s3"} and parsed.netloc))):
            raise ValueError("backup receipt location must be an absolute path or safe https/s3 reference")
        result.append({
            "id": receipt_id, "location": location, "manifest_sha256": digest,
            **{field: iso(timestamp(item[field], field)) if item.get(field) else None
               for field in ("verified_at", "restore_tested_at")},
        })
    return sorted(result, key=lambda row: row["id"])


def clean_rows(raw, sites, version):
    if not isinstance(raw, list) or len(raw) > MAX_ROWS:
        raise ValueError("events must be a bounded aggregate list")
    result, seen, discarded = [], set(), 0
    for row in raw:
        if not isinstance(row, dict):
            raise ValueError("aggregate row must be an object")
        props = row.get("properties")
        if (row.get("site") not in sites or row.get("event") not in EVENTS
                or not isinstance(props, dict) or props.get("offer") != OFFER
                or props.get("offer_version") != version
                or any(not isinstance(props.get(k), str) or props[k] not in VOCABULARIES[k]
                       for k in REQUIRED_PROPERTIES)):
            discarded += 1
            continue
        count, width = row.get("count"), row.get("bucket_seconds")
        if type(count) is not int or count < 0 or count > 2**63 - 1:
            raise ValueError("aggregate count must be a nonnegative integer")
        if type(width) is not int or not 1 <= width <= 3600:
            raise ValueError("bucket_seconds must be an integer from 1 to 3600")
        start = timestamp(row.get("bucket_start"), "bucket_start")
        clean = {k: props[k] for k, allowed in VOCABULARIES.items()
                 if isinstance(props.get(k), str) and props[k] in allowed}
        key = (row["site"], row["event"], iso(start), width, canonical(clean))
        if key in seen:
            raise ValueError("duplicate aggregate key; collector must return disjoint buckets")
        seen.add(key)
        result.append({"site": row["site"], "event": row["event"], "start": start,
                       "end": start + timedelta(seconds=width), "count": count, "properties": clean})
    return result, discarded


def period_report(bounds, rows, source):
    start, end, complete = bounds
    reason = None
    if start == end:
        reason = "empty_period"
    elif source["status"] != "ok":
        reason = "instrumentation_unknown" if source["status"] == "unknown" else "source_failed"
    elif (timestamp(source["coverage_start"], "coverage_start") > start
          or timestamp(source["coverage_end"], "coverage_end") < end):
        reason = "incomplete_source_coverage"
    selected = [row for row in rows if row["start"] < end and row["end"] > start]
    if reason is None and any(row["start"] < start or row["end"] > end for row in selected):
        reason = "bucket_crosses_period_boundary"
    metrics = {event: None if reason else 0 for event in EVENTS}
    products = {product: {event: None if reason else 0 for event in ("evidence_view", "evidence_code_open")}
                for product in sorted(VOCABULARIES["product"] | {"unclassified"})}
    errors = {error: None if reason else 0 for error in sorted(VOCABULARIES["error"] | {"unclassified"})}
    locales = {locale: {event: None if reason else 0 for event in EVENTS}
               for locale in sorted(VOCABULARIES["locale"])}
    if not reason:
        for row in selected:
            event, count, props = row["event"], row["count"], row["properties"]
            metrics[event] += count
            locales[props["locale"]][event] += count
            if event in {"evidence_view", "evidence_code_open"}:
                products[props.get("product", "unclassified")][event] += count
            if event == "form_error":
                errors[props.get("error", "unclassified")] += count
    return {"start_inclusive": iso(start), "end_exclusive": iso(end),
            "local_start": start.isoformat(), "local_end": end.isoformat(),
            "calendar_complete": complete, "coverage": "unknown" if reason else "observed",
            "unknown_reason": reason, "metrics": metrics, "products": products,
            "errors": errors, "locales": locales}


def site_period_report(bounds, rows, source):
    """Keep post-launch evidence visible without promoting it to whole-period coverage."""
    result = period_report(bounds, rows, source)
    if result["unknown_reason"] == "incomplete_source_coverage" and source["status"] == "ok":
        start, end, _ = bounds
        observed_start = max(start, timestamp(source["coverage_start"], "coverage_start")).astimezone(TZ)
        observed_end = min(end, timestamp(source["coverage_end"], "coverage_end")).astimezone(TZ)
        if observed_start < observed_end:
            subset = period_report((observed_start, observed_end, False), rows, source)
            if subset["coverage"] == "observed":
                result["observed_partial"] = subset
    return result


def difference(current, previous):
    if current is None or previous is None:
        return {"current": current, "previous": previous, "delta": None, "percent_change": None}
    return {"current": current, "previous": previous, "delta": current - previous,
            "percent_change": round((current - previous) * 100 / previous, 2) if previous else None}


def build_report(data):
    if not isinstance(data, dict) or data.get("schema_version") != "plausible-weekly-input-v1":
        raise ValueError("unsupported input schema_version")
    version = data.get("offer_version")
    if not isinstance(version, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", version):
        raise ValueError("offer_version is required as YYYY-MM-DD")
    try:
        datetime.strptime(version, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("offer_version must be a valid calendar date") from exc
    sites = data.get("sites")
    if (not isinstance(sites, list) or not sites
            or any(not isinstance(site, str) or site not in SITES for site in sites)
            or len(sites) != len(set(sites))):
        raise ValueError("sites must contain unique supported site names")
    as_of = timestamp(data.get("as_of"), "as_of")
    source = clean_source(data.get("source"))
    if as_of > timestamp(source["extracted_at"], "extracted_at"):
        raise ValueError("as_of cannot be later than extraction")
    rows, discarded = clean_rows(data.get("events"), sites, version)
    bounds = windows(as_of)
    by_site = {}
    for site in sorted(sites):
        site_rows = [row for row in rows if row["site"] == site]
        site_source = source
        if "site_coverage" in source and source["status"] == "ok":
            site_source = {**source, **source["site_coverage"].get(site, {"status": "unknown"})}
        periods = {key: site_period_report(value, site_rows, site_source) for key, value in bounds.items()}
        by_site[site] = {"instrumented_since": site_source.get("instrumented_since"), "periods": periods, "comparison": {
            event: difference(periods["current"]["metrics"][event],
                              periods["previous_comparable"]["metrics"][event]) for event in EVENTS}}
    report = {"schema_version": "plausible-weekly-report-v1", "as_of": iso(as_of),
              "timezone": TIMEZONE, "week": as_of.astimezone(TZ).strftime("%G-W%V"),
              "offer": OFFER, "offer_version": version, "source": source,
              "discarded_aggregate_rows": discarded, "sites": by_site,
              "backup_receipts": clean_receipts(data.get("backup_receipts", [])),
              "limitations": [
                  "Counts are aggregate events, not unique visitors or individual funnels.",
                  "form_success is service acceptance, not a qualified lead or acquired customer.",
                  "Current week is partial; comparison uses the same local cutoff one week earlier.",
                  "Collection coverage does not prove every visitor was tracked; blocking and opt-out remain possible.",
                  "Source coverage and backup receipts are attestations by the collector; this renderer performs no restore.",
              ]}
    report["report_id"] = hashlib.sha256(canonical(report).encode()).hexdigest()
    return report


def render_markdown(report):
    def display(value):
        return "unknown" if value is None else str(value)

    lines = [f"# Institutional navigation — {report['week']}", "",
             f"Cutoff: **{report['as_of']}** · Timezone: **{report['timezone']}**.",
             f"Offer: `{report['offer']}` · Version: `{report['offer_version']}`.",
             "Current week is partial. Counts below describe events, not identified people or customers.", "",
             "## Evidence quality", "",
             f"- Source: `{report['source']['system']}` / `{report['source']['query_version']}`.",
             f"- Extraction: {report['source']['extracted_at']}; status: **{report['source']['status']}**.",
             f"- Coverage: {display(report['source']['coverage_start'])} inclusive to {display(report['source']['coverage_end'])} exclusive.",
             f"- Latest observed source event: {display(report['source']['watermark'])} (not a query-health timestamp).",
             f"- Excluded aggregate rows outside the contract: {report['discarded_aggregate_rows']}.",
             f"- Report SHA-256: `{report['report_id']}`.", ""]
    for site, content in report["sites"].items():
        lines += [f"## {site}", ""]
        for key, period in content["periods"].items():
            label = key.replace("_", " ")
            lines.append(f"- {label}: {period['local_start']} to {period['local_end']} (exclusive); **{period['coverage']}**"
                         + (f" — {period['unknown_reason']}" if period["unknown_reason"] else "") + ".")
        lines += ["", "| Event | Current partial | Previous same cutoff | Change | Change % | Last complete week |",
                  "|---|---:|---:|---:|---:|---:|"]
        for event in EVENTS:
            values = content["comparison"][event]
            lines.append(f"| `{event}` | {display(values['current'])} | {display(values['previous'])} | "
                         f"{display(values['delta'])} | {display(values['percent_change'])} | "
                         f"{display(content['periods']['last_complete']['metrics'][event])} |")
        partial = content["periods"]["current"].get("observed_partial")
        if partial:
            lines += ["", "### Observed since instrumentation — incomplete week", "",
                      f"Observed interval: {partial['local_start']} to {partial['local_end']} (exclusive).",
                      "These counts cover only this interval. No full-week or prior-week comparison is inferred.", "",
                      "| Event | Observed subset |", "|---|---:|"]
            for event, count in partial["metrics"].items():
                lines.append(f"| `{event}` | {display(count)} |")
        lines += ["", "Percent change is unknown when the previous count is zero or either period lacks coverage.", ""]
        current = partial or content["periods"]["current"]
        if partial:
            lines += ["The product and error breakdowns below cover the observed subset only.", ""]
        lines += ["### Portfolio evidence — current partial week", "",
                  "| Product | Evidence exposed | Code opened |", "|---|---:|---:|"]
        for product, metrics in current["products"].items():
            lines.append(f"| {product} | {display(metrics['evidence_view'])} | {display(metrics['evidence_code_open'])} |")
        lines += ["", "### Form reliability — current partial week", "", "| Error category | Events |", "|---|---:|"]
        for error, count in current["errors"].items():
            lines.append(f"| {error} | {display(count)} |")
        lines += ["", "Unclassified means an absent or invalid optional category; it remains in the event total.",
                  "Do not subtract event totals to infer abandonment. Validation and challenge errors can occur before submission.", ""]
    lines += ["## Preservation evidence", ""]
    if not report["backup_receipts"]:
        lines.append("**Unknown:** no backup receipt was supplied. This report is not evidence of a recoverable backup.")
    for receipt in report["backup_receipts"]:
        lines += [f"- `{receipt['id']}`: <{receipt['location']}>.",
                  f"  Manifest SHA-256: `{receipt['manifest_sha256']}`; verified: {display(receipt['verified_at'])}; "
                  f"restore tested: {display(receipt['restore_tested_at'])}."]
    lines += ["", "## Sunday Flight Deck review", "",
              "- Check missing coverage and backup/restore evidence before interpreting changes.",
              "- Compare portfolio interest and qualification activity against the same elapsed interval.",
              "- Investigate error categories and recurring product interest; record a bounded improvement hypothesis.",
              "- Assess actual qualification, response capacity and commercial outcomes separately in their owning systems.",
              "- Attach this report as evidence in the appropriate Workstream; it creates no Signal, decision or execution automatically.",
              "", "## Interpretation limits", ""]
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines) + "\n"


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--json-output", required=True, type=Path)
    parser.add_argument("--markdown-output", required=True, type=Path)
    args = parser.parse_args(argv)
    paths = [args.input.resolve(), args.json_output.resolve(), args.markdown_output.resolve()]
    if len(set(paths)) != 3:
        parser.error("input and output paths must be distinct")
    try:
        with args.input.open("rb") as source:
            raw = source.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("input exceeds the 32 MiB limit")
        report = build_report(json.loads(raw))
        atomic_write(args.json_output, json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
        atomic_write(args.markdown_output, render_markdown(report))
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"Weekly report failed: {type(exc).__name__}; validate input and output accessibility.", file=sys.stderr)
        return 1
    print(f"Weekly report written; report_id={report['report_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
