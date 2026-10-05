"""Offline evidence semantics and failure checks for the weekly report."""

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "plausible" / "weekly_report.py"
SPEC = importlib.util.spec_from_file_location("plausible_weekly_report", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def fixture():
    return {
        "schema_version": "plausible-weekly-input-v1",
        "as_of": "2026-10-11T18:00:00-03:00",
        "offer_version": "2026-10-05", "sites": ["rbx.ia.br", "rbxsystems.ch"],
        "source": {"system": "plausible-clickhouse", "query_version": "test-events-v1",
                   "status": "ok", "extracted_at": "2026-10-11T21:01:00Z",
                   "coverage_start": "2026-09-28T03:00:00Z",
                   "coverage_end": "2026-10-11T21:00:00Z", "watermark": None},
        "events": [], "backup_receipts": [],
    }


def event(at, count=1, name="offer_view", site="rbx.ia.br", **properties):
    return {"site": site, "event": name, "count": count,
            "bucket_start": at, "bucket_seconds": 60,
            "properties": {"offer": "engineering-partnership", "offer_version": "2026-10-05",
                           "locale": "pt-BR", "surface": "partnership", "entry": "offer", **properties}}


class WeeklyReportTests(unittest.TestCase):
    def test_sunday_includes_current_week_but_compares_identical_local_cutoff(self):
        data = fixture()
        data["events"] = [event("2026-10-05T03:00:00Z", 3), event("2026-10-11T20:59:00Z", 5),
                          event("2026-09-28T03:00:00Z", 4), event("2026-10-04T22:00:00Z", 10)]
        report = MODULE.build_report(data)
        periods = report["sites"]["rbx.ia.br"]["periods"]
        self.assertEqual(periods["current"]["start_inclusive"], "2026-10-05T03:00:00Z")
        self.assertEqual(periods["current"]["metrics"]["offer_view"], 8)
        self.assertEqual(periods["previous_comparable"]["metrics"]["offer_view"], 4)
        self.assertEqual(periods["last_complete"]["metrics"]["offer_view"], 14)
        self.assertFalse(periods["current"]["calendar_complete"])
        self.assertTrue(periods["last_complete"]["calendar_complete"])
        self.assertEqual(report["sites"]["rbx.ia.br"]["comparison"]["offer_view"]["percent_change"], 100)

    def test_local_midnight_not_utc_midnight_starts_week(self):
        sunday = MODULE.windows(MODULE.timestamp("2026-10-12T02:59:00Z", "test"))
        monday = MODULE.windows(MODULE.timestamp("2026-10-12T03:00:00Z", "test"))
        self.assertEqual(MODULE.iso(sunday["current"][0]), "2026-10-05T03:00:00Z")
        self.assertEqual(MODULE.iso(monday["current"][0]), "2026-10-12T03:00:00Z")
        self.assertEqual(MODULE.iso(monday["last_complete"][0]), "2026-10-05T03:00:00Z")

    def test_iso_week_year_boundary(self):
        data = fixture()
        data["as_of"] = "2027-01-03T18:00:00-03:00"
        data["source"].update(extracted_at="2027-01-03T21:01:00Z", coverage_end="2027-01-03T21:00:00Z")
        self.assertEqual(MODULE.build_report(data)["week"], "2026-W53")

    def test_failure_is_unknown_even_when_events_are_present(self):
        data = fixture()
        data["source"].update(status="error", coverage_start=None, coverage_end=None)
        data["events"] = [event("2026-10-06T12:00:00Z", 5)]
        periods = MODULE.build_report(data)["sites"]["rbx.ia.br"]["periods"]
        self.assertTrue(all(p["metrics"]["offer_view"] is None for p in periods.values()))
        self.assertEqual(periods["current"]["unknown_reason"], "source_failed")

    def test_incomplete_coverage_is_unknown_only_for_affected_period(self):
        data = fixture()
        data["source"]["coverage_start"] = "2026-10-05T03:00:00Z"
        periods = MODULE.build_report(data)["sites"]["rbx.ia.br"]["periods"]
        self.assertEqual(periods["current"]["metrics"]["offer_view"], 0)
        self.assertIsNone(periods["last_complete"]["metrics"]["offer_view"])

    def test_observed_zero_does_not_require_recent_event_watermark(self):
        report = MODULE.build_report(fixture())
        comparison = report["sites"]["rbx.ia.br"]["comparison"]["form_success"]
        self.assertEqual(comparison["current"], 0)
        self.assertEqual(comparison["delta"], 0)
        self.assertIsNone(comparison["percent_change"])

    def test_bucket_crossing_cutoff_is_unknown_without_fractional_estimation(self):
        data = fixture()
        data["events"] = [event("2026-10-11T20:59:30Z", 8)]
        periods = MODULE.build_report(data)["sites"]["rbx.ia.br"]["periods"]
        self.assertEqual(periods["current"]["unknown_reason"], "bucket_crosses_period_boundary")
        self.assertIsNone(periods["current"]["metrics"]["offer_view"])
        self.assertEqual(periods["last_complete"]["metrics"]["offer_view"], 0)

    def test_sites_products_error_and_locale_remain_separate(self):
        data = fixture()
        data["events"] = [event("2026-10-06T12:00:00Z", 2, "evidence_code_open", product="robson"),
                          event("2026-10-06T12:00:00Z", 4, "form_error", "rbxsystems.ch", error="challenge", locale="en")]
        sites = MODULE.build_report(data)["sites"]
        br = sites["rbx.ia.br"]["periods"]["current"]
        ch = sites["rbxsystems.ch"]["periods"]["current"]
        self.assertEqual(br["products"]["robson"]["evidence_code_open"], 2)
        self.assertEqual(br["errors"]["challenge"], 0)
        self.assertEqual(ch["errors"]["challenge"], 4)
        self.assertEqual(ch["locales"]["en"]["form_error"], 4)
        self.assertEqual(ch["locales"]["pt-BR"]["form_error"], 0)

    def test_extra_properties_and_source_fields_never_leak(self):
        data = fixture()
        data["email"] = "never-export@example.test"
        data["source"]["debug_sql"] = "never-export@example.test"
        data["events"] = [event("2026-10-06T12:00:00Z", email="never-export@example.test",
                                url="https://example.test/?email=never-export@example.test", product="private-client-name")]
        report = MODULE.build_report(data)
        self.assertNotIn("never-export", json.dumps(report) + MODULE.render_markdown(report))
        self.assertNotIn("private-client-name", json.dumps(report))
        self.assertEqual(report["sites"]["rbx.ia.br"]["periods"]["current"]["metrics"]["offer_view"], 1)

    def test_unclassified_dimensions_reconcile_to_event_totals(self):
        data = fixture()
        data["events"] = [event("2026-10-06T12:00:00Z", 3, "evidence_code_open", product="private-client"),
                          event("2026-10-06T12:00:00Z", 2, "form_error")]
        current = MODULE.build_report(data)["sites"]["rbx.ia.br"]["periods"]["current"]
        self.assertEqual(current["products"]["unclassified"]["evidence_code_open"], 3)
        self.assertEqual(sum(current["errors"].values()), current["metrics"]["form_error"])
        self.assertEqual(current["errors"]["unclassified"], 2)

    def test_invalid_required_property_types_are_discarded_without_leaking(self):
        data = fixture()
        data["events"] = [event("2026-10-06T12:00:00Z", locale={"email": "never-export@example.test"})]
        report = MODULE.build_report(data)
        self.assertEqual(report["discarded_aggregate_rows"], 1)
        self.assertNotIn("never-export", json.dumps(report))

    def test_offer_version_is_required_and_other_versions_excluded(self):
        data = fixture()
        del data["offer_version"]
        with self.assertRaises(ValueError):
            MODULE.build_report(data)
        data = fixture()
        data["events"] = [event("2026-10-06T12:00:00Z", offer_version="2026-10-01")]
        report = MODULE.build_report(data)
        self.assertEqual(report["discarded_aggregate_rows"], 1)
        self.assertEqual(report["sites"]["rbx.ia.br"]["periods"]["current"]["metrics"]["offer_view"], 0)

    def test_repeat_and_row_order_are_idempotent(self):
        data = fixture()
        data["events"] = [event("2026-10-06T12:00:00Z", 2), event("2026-10-07T12:00:00Z", 4)]
        one = MODULE.build_report(data)
        data["events"].reverse()
        two = MODULE.build_report(data)
        self.assertEqual(one, two)
        self.assertEqual(MODULE.render_markdown(one), MODULE.render_markdown(two))

    def test_duplicate_buckets_fail_instead_of_double_counting(self):
        data = fixture()
        row = event("2026-10-06T12:00:00Z")
        data["events"] = [row, copy.deepcopy(row)]
        with self.assertRaisesRegex(ValueError, "duplicate aggregate"):
            MODULE.build_report(data)

    def test_invalid_timestamp_and_counts_are_rejected(self):
        for key, value in (("bucket_start", "2026-10-06T12:00:00"), ("count", -1),
                           ("count", True), ("bucket_seconds", 0)):
            with self.subTest(key=key, value=value):
                data = fixture()
                row = event("2026-10-06T12:00:00Z")
                row[key] = value
                data["events"] = [row]
                with self.assertRaises(ValueError):
                    MODULE.build_report(data)

    def test_coverage_and_watermark_cannot_claim_future_observation(self):
        for field in ("coverage_end", "watermark"):
            with self.subTest(field=field):
                data = fixture()
                data["source"][field] = "2026-10-12T00:00:00Z"
                with self.assertRaises(ValueError):
                    MODULE.build_report(data)

    def test_backup_receipt_is_explicit_and_credential_urls_are_rejected(self):
        data = fixture()
        data["backup_receipts"] = [{"id": "backup-01", "location": "https://example.test/receipt.json",
                                    "manifest_sha256": "a" * 64, "verified_at": "2026-10-11T19:00:00Z"}]
        report = MODULE.build_report(data)
        self.assertIsNone(report["backup_receipts"][0]["restore_tested_at"])
        self.assertIn("restore tested: unknown", MODULE.render_markdown(report))
        data["backup_receipts"][0]["location"] += "?token=must-not-leak"
        with self.assertRaises(ValueError):
            MODULE.build_report(data)

    def test_cli_produces_real_markdown_and_repeatable_json(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source, target_json, target_md = base / "input.json", base / "weekly.json", base / "weekly.md"
            source.write_text(json.dumps(fixture()))
            args = ["--input", str(source), "--json-output", str(target_json), "--markdown-output", str(target_md)]
            self.assertEqual(MODULE.main(args), 0)
            first = target_json.read_bytes()
            self.assertEqual(MODULE.main(args), 0)
            self.assertEqual(first, target_json.read_bytes())
            self.assertIn("Sunday Flight Deck review", target_md.read_text())
            self.assertIn("no backup receipt was supplied", target_md.read_text())


if __name__ == "__main__":
    unittest.main()
