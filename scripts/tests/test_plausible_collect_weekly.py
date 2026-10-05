"""Offline checks for the bounded aggregate collector and deployment coverage."""

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "plausible" / "collect_weekly.py"
SPEC = importlib.util.spec_from_file_location("plausible_collect_weekly", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
AS_OF = datetime(2026, 10, 11, 21, 0, tzinfo=timezone.utc)
EXTRACTED = datetime(2026, 10, 11, 21, 1, tzinfo=timezone.utc)
LAUNCH = MODULE.report.timestamp("2026-10-05T18:34:48Z", "test")


def aggregate(**changes):
    row = {"site": "rbxsystems.ch", "event": "evidence_code_open", "count": "4",
           "bucket_start": "2026-10-05T18:34:48Z", "bucket_seconds": 12,
           "watermark": "2026-10-05T18:34:59Z", "locale": "en", "surface": "products",
           "entry": "gallery", "product": "robson", "error": "", "destination": ""}
    row.update(changes)
    return json.dumps(row).encode() + b"\n"


class CollectWeeklyTests(unittest.TestCase):
    def collect(self, raw=b"", instrumented=None, runner=None):
        return MODULE.collect(AS_OF, {"rbxsystems.ch": LAUNCH} if instrumented is None else instrumented,
                              "/unused/kubeconfig", runner=runner or (lambda query, config, parameters: raw),
                              clock=lambda: EXTRACTED)

    def test_query_is_one_bounded_select_and_never_exports_identifiers(self):
        calls = []
        self.collect(runner=lambda query, config, parameters: calls.append((query, config, parameters)) or b"")
        self.assertEqual(len(calls), 1)
        query = calls[0][0]
        for setting in ("max_execution_time=20", "max_memory_usage=268435456", "max_result_bytes=33554432",
                        "result_overflow_mode='throw'", "max_result_rows=250000", "max_threads=2"):
            self.assertIn(setting, query)
        self.assertIn("offer_version_value = {offer_version:String}", query)
        self.assertIn("toDateTime({as_of_epoch:UInt32}", query)
        self.assertEqual(calls[0][2]["offer_version"], "2026-10-05")
        self.assertEqual(calls[0][2]["site_1_enabled"], "0")
        self.assertEqual(calls[0][2]["site_2_enabled"], "1")
        self.assertNotIn(str(int(LAUNCH.timestamp())), query)
        for forbidden in ("user_id", "session_id", "pathname", "hostname", "url", "INSERT", "ALTER", "DELETE"):
            self.assertNotIn(forbidden, query)
        self.assertNotIn("SELECT *", query)

    def test_first_deployment_bucket_is_partial_and_not_backdated(self):
        data = self.collect(aggregate())
        self.assertEqual(data["events"][0]["bucket_seconds"], 12)
        coverage = data["source"]["site_coverage"]
        self.assertEqual(coverage["rbxsystems.ch"]["coverage_start"], "2026-10-05T18:34:48Z")
        self.assertEqual(coverage["rbx.ia.br"]["status"], "unknown")
        rendered = MODULE.report.build_report(data)
        ch = rendered["sites"]["rbxsystems.ch"]["periods"]["current"]
        br = rendered["sites"]["rbx.ia.br"]["periods"]["current"]
        self.assertIsNone(ch["metrics"]["evidence_code_open"])
        self.assertEqual(ch["observed_partial"]["metrics"]["evidence_code_open"], 4)
        self.assertEqual(ch["observed_partial"]["start_inclusive"], "2026-10-05T18:34:48Z")
        self.assertEqual(br["unknown_reason"], "instrumentation_unknown")
        self.assertIsNone(br["metrics"]["evidence_code_open"])
        self.assertIsNone(rendered["sites"]["rbxsystems.ch"]["comparison"]["evidence_code_open"]["delta"])
        self.assertIn("Observed since instrumentation", MODULE.report.render_markdown(rendered))

    def test_successful_empty_query_is_zero_only_within_known_coverage(self):
        rendered = MODULE.report.build_report(self.collect())
        ch = rendered["sites"]["rbxsystems.ch"]["periods"]
        self.assertEqual(ch["current"]["observed_partial"]["metrics"]["form_success"], 0)
        self.assertIsNone(ch["last_complete"]["metrics"]["form_success"])
        self.assertNotIn("observed_partial", ch["last_complete"])

    def test_clickhouse_quoted_integer_encoding_is_accepted(self):
        data = self.collect(aggregate(bucket_seconds="12"))
        self.assertEqual(data["source"]["status"], "ok")
        self.assertEqual(data["events"][0]["bucket_seconds"], 12)

    def test_unknown_all_sites_stays_unknown(self):
        data = self.collect(instrumented={})
        rendered = MODULE.report.build_report(data)
        self.assertTrue(all(site["periods"]["current"]["metrics"]["offer_view"] is None
                            for site in rendered["sites"].values()))

    def test_failure_is_reportable_unknown_without_error_text(self):
        def fail(query, config, parameters):
            raise MODULE.CollectionError("credential=never-export-this")
        data = self.collect(runner=fail)
        self.assertEqual(data["source"]["status"], "error")
        self.assertNotIn("never-export", json.dumps(data))
        rendered = MODULE.report.build_report(data)
        self.assertEqual(rendered["sites"]["rbxsystems.ch"]["periods"]["current"]["unknown_reason"], "source_failed")

    def test_malformed_or_out_of_scope_result_is_failure_not_partial_success(self):
        for raw in (b"not-json", aggregate(site="rbx.ia.br"), aggregate(count="-2"),
                    aggregate(bucket_start="2026-10-05T18:34:00Z", bucket_seconds=60),
                    aggregate() + aggregate(), aggregate(locale="unknown")):
            with self.subTest(raw=raw[:40]):
                data = self.collect(raw)
                self.assertEqual(data["source"]["status"], "error")
                self.assertEqual(data["events"], [])

    def test_extra_fields_and_invalid_optional_values_never_leave_collector(self):
        data = self.collect(aggregate(email="never-export@example.test", product="private-client"))
        self.assertEqual(data["source"]["status"], "ok")
        self.assertNotIn("never-export", json.dumps(data))
        self.assertNotIn("private-client", json.dumps(data))

    def test_query_runner_uses_fixed_argv_and_timeout(self):
        parameters = MODULE.build_parameters(AS_OF, {"rbxsystems.ch": LAUNCH})
        with mock.patch.object(MODULE.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")) as run:
            MODULE.run_query(MODULE.build_query(), "/safe/kubeconfig", parameters)
        argv = run.call_args.args[0]
        self.assertEqual(argv[-2:], ["--query", MODULE.build_query()])
        self.assertIn(f"--param_as_of_epoch={int(AS_OF.timestamp())}", argv)
        self.assertIn("--request-timeout=30s", argv)
        self.assertEqual(run.call_args.kwargs["timeout"], 35)
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_malicious_values_cannot_enter_sql_or_parameter_names(self):
        attacks = ["rbxsystems.ch=2026-10-05T18:34:48Z'; DROP TABLE events_v2; --",
                   "rbxsystems.ch;DROP TABLE events_v2=2026-10-05T18:34:48Z"]
        for attack in attacks:
            with self.subTest(attack=attack), self.assertRaises(ValueError):
                MODULE.parse_instrumented([attack])
        with self.assertRaises(ValueError):
            MODULE.sql_list(["locale'); DROP TABLE events_v2; --"])
        with self.assertRaises(ValueError):
            MODULE.build_parameters(AS_OF, {"site_id=1 OR 1=1": LAUNCH})
        parameters = MODULE.build_parameters(AS_OF, {"rbxsystems.ch": LAUNCH})
        parameters["x;DROP TABLE events_v2"] = "1"
        with mock.patch.object(MODULE.subprocess, "run") as run, self.assertRaises(ValueError):
            MODULE.run_query(MODULE.build_query(), "/safe/kubeconfig", parameters)
        run.assert_not_called()

    def test_even_sql_like_parameter_values_are_separate_from_query_text(self):
        parameters = MODULE.build_parameters(AS_OF, {"rbxsystems.ch": LAUNCH})
        malicious = "x'; DROP TABLE events_v2; --"
        parameters["offer"] = malicious
        with mock.patch.object(MODULE.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")) as run:
            MODULE.run_query(MODULE.build_query(), "/safe/kubeconfig", parameters)
        argv = run.call_args.args[0]
        self.assertIn("--param_offer=" + malicious, argv)
        self.assertNotIn(malicious, argv[-1])
        self.assertIn("{offer:String}", argv[-1])

    def test_instrumentation_scope_and_cutoff_are_validated(self):
        for values in (["unknown.example=2026-10-05T00:00:00Z"],
                       ["rbx.ia.br=2026-10-05T00:00:00"],
                       ["rbx.ia.br=2026-10-05T00:00:00Z"] * 2):
            with self.subTest(values=values), self.assertRaises(ValueError):
                MODULE.parse_instrumented(values)
        with self.assertRaises(ValueError):
            MODULE.collect(AS_OF.replace(second=1), {}, "/unused", clock=lambda: EXTRACTED)
        with self.assertRaises(ValueError):
            MODULE.collect(EXTRACTED, {}, "/unused", clock=lambda: AS_OF)

    def test_completed_coverage_becomes_comparable_only_after_full_weeks(self):
        before = MODULE.report.timestamp("2026-09-01T00:00:00Z", "test")
        rendered = MODULE.report.build_report(self.collect(instrumented={"rbxsystems.ch": before}))
        ch = rendered["sites"]["rbxsystems.ch"]["periods"]
        self.assertEqual(ch["current"]["metrics"]["offer_view"], 0)
        self.assertEqual(ch["last_complete"]["metrics"]["offer_view"], 0)


if __name__ == "__main__":
    unittest.main()
