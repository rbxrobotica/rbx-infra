"""Tests of scripts/scan-repository-baseline.py with synthetic reports.

No scanner and no network: the reports are written here, in the shape Trivy
0.76.2 and osv-scanner 2.7.0 write them.
"""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scan-repository-baseline.py"
SPEC = importlib.util.spec_from_file_location("scan_repository_baseline", SCRIPT)
baseline = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = baseline
SPEC.loader.exec_module(baseline)

RBAC = "apps/prod/agents/rbac.yml"
DEPLOY = "apps/prod/site/deployment.yml"
EXEC = "Role 'writer' should not have access to resource '[\"pods/exec\"]'"
WILDCARD = "Role 'writer' shouldn't manage all resources at the namespace 'one'"
ROOTFS = "Container 'web' of Deployment 'site' should set 'securityContext.readOnlyRootFilesystem' to true"
GO_MOD = "scripts/tool/go.mod"

BASELINE = f"""
[[trivy]]
id = "KSV-0112"
expires = 2026-12-28
statement = "Deliberate grant, not reviewed yet."

[trivy.findings]
"{RBAC}" = ["{WILDCARD}"]

[[trivy]]
id = "KSV-0053"
expires = 2026-12-28
statement = "Deliberate grant, not reviewed yet."

[trivy.findings]
"{RBAC}" = [
  {json.dumps(EXEC)},
  {json.dumps(EXEC)},
]

[[trivy]]
id = "KSV-0014"
expires = 2027-02-27
statement = "Hardening backlog."

[trivy.findings]
"{DEPLOY}" = ["{ROOTFS}"]

[[osv]]
id = "GO-2026-6974"
expires = 2026-12-28
statement = "Fixed in 1.47.0."

[osv.findings]
"{GO_MOD}" = ["go.opentelemetry.io/otel/trace@1.41.0"]
"""


def misconfiguration(check, message, severity="HIGH", status="FAIL"):
    return {"ID": check, "Message": message, "Severity": severity, "Status": status}


def trivy_report(*targets):
    return {"SchemaVersion": 2, "Results": [
        {"Target": target, "Class": "config", "Type": "kubernetes", "Misconfigurations": items}
        for target, items in targets]}


def osv_report(*sources):
    results = []
    for path, packages in sources:
        results.append({"source": {"path": f"/repo/{path}", "type": "lockfile"}, "packages": [
            {"package": {"name": name, "version": version, "ecosystem": "Go"},
             "groups": [{"ids": ids, "aliases": aliases, "max_severity": "5.3"}]}
            for name, version, ids, aliases in packages]})
    return {"results": results}


TRIVY_BASE = (
    (RBAC, [misconfiguration("KSV-0112", WILDCARD, "CRITICAL"),
            misconfiguration("KSV-0053", EXEC), misconfiguration("KSV-0053", EXEC)]),
    (DEPLOY, [misconfiguration("KSV-0014", ROOTFS)]),
)
OTEL_TRACE = ("go.opentelemetry.io/otel/trace", "1.41.0",
              ["GO-2026-6974", "GHSA-q7gw-24fr-5p69"], ["CVE-2026-128085"])
OSV_BASE = ((GO_MOD, [OTEL_TRACE]),)


class BaselineCheck(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def run_check(self, trivy=None, osv=None, text=BASELINE, today="2026-11-28", write=True):
        paths = {name: self.root / name for name in ("baseline.toml", "trivy.json", "osv.json")}
        if write:
            paths["baseline.toml"].write_text(text)
            paths["trivy.json"].write_text(json.dumps(trivy_report(*TRIVY_BASE) if trivy is None else trivy))
            paths["osv.json"].write_text(json.dumps(osv_report(*OSV_BASE) if osv is None else osv))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = baseline.main([
                "--baseline", str(paths["baseline.toml"]), "--trivy", str(paths["trivy.json"]),
                "--osv", str(paths["osv.json"]), "--osv-root", "/repo", "--today", today])
        return status, out.getvalue(), err.getvalue()

    def test_reports_equal_to_the_baseline_pass(self):
        status, out, _ = self.run_check()
        self.assertEqual(status, 0, out)
        self.assertIn("trivy: 4 reported, 4 covered by the baseline, 0 not listed, 0 past", out)
        self.assertIn("osv: 1 reported, 1 covered by the baseline, 0 not listed, 0 past", out)
        self.assertNotIn("note:", out)

    def test_another_role_in_a_listed_file_fails(self):
        other = "Role 'writer' shouldn't manage all resources at the namespace 'two'"
        report = trivy_report(
            (RBAC, TRIVY_BASE[0][1] + [misconfiguration("KSV-0112", other, "CRITICAL")]), TRIVY_BASE[1])
        status, out, _ = self.run_check(trivy=report)
        self.assertEqual(status, 1, out)
        self.assertIn(f"NOT LISTED  KSV-0112 (CRITICAL) {RBAC}: {other}", out)
        self.assertIn("1 not listed", out)

    def test_one_more_occurrence_of_a_listed_message_fails(self):
        report = trivy_report(
            (RBAC, TRIVY_BASE[0][1] + [misconfiguration("KSV-0053", EXEC)]), TRIVY_BASE[1])
        status, out, _ = self.run_check(trivy=report)
        self.assertEqual(status, 1, out)
        self.assertIn("trivy: 5 reported, 4 covered by the baseline, 1 not listed", out)

    def test_another_container_in_a_listed_file_fails(self):
        other = ROOTFS.replace("'web'", "'sidecar'")
        report = trivy_report(
            TRIVY_BASE[0], (DEPLOY, TRIVY_BASE[1][1] + [misconfiguration("KSV-0014", other)]))
        status, out, _ = self.run_check(trivy=report)
        self.assertEqual(status, 1, out)
        self.assertIn(f"{DEPLOY}: {other}", out)

    def test_a_listed_message_in_another_file_fails(self):
        report = trivy_report(*TRIVY_BASE, ("apps/prod/copy/deployment.yml",
                                            [misconfiguration("KSV-0014", ROOTFS)]))
        status, out, _ = self.run_check(trivy=report)
        self.assertEqual(status, 1, out)
        self.assertIn("NOT LISTED  KSV-0014 (HIGH) apps/prod/copy/deployment.yml", out)

    def test_another_check_on_a_listed_resource_fails(self):
        report = trivy_report(
            TRIVY_BASE[0], (DEPLOY, TRIVY_BASE[1][1] + [misconfiguration("KSV-0118", ROOTFS)]))
        status, out, _ = self.run_check(trivy=report)
        self.assertEqual(status, 1, out)
        self.assertIn("NOT LISTED  KSV-0118", out)

    def test_a_listed_advisory_in_another_lockfile_fails(self):
        report = osv_report(*OSV_BASE, ("apps/other/go.mod", [OTEL_TRACE]))
        status, out, _ = self.run_check(osv=report)
        self.assertEqual(status, 1, out)
        self.assertIn("NOT LISTED  GO-2026-6974/GHSA-q7gw-24fr-5p69/CVE-2026-128085 (5.3) "
                      "apps/other/go.mod: go.opentelemetry.io/otel/trace@1.41.0", out)

    def test_a_listed_advisory_for_another_version_or_package_fails(self):
        for name, version in (("go.opentelemetry.io/otel/trace", "1.40.0"),
                              ("go.opentelemetry.io/otel/sdk", "1.41.0")):
            with self.subTest(package=name, version=version):
                report = osv_report((GO_MOD, [OTEL_TRACE, (name, version, ["GO-2026-6974"], [])]))
                status, out, _ = self.run_check(osv=report)
                self.assertEqual(status, 1, out)
                self.assertIn(f"{GO_MOD}: {name}@{version}", out)

    def test_another_advisory_for_a_listed_package_fails(self):
        report = osv_report((GO_MOD, [OTEL_TRACE, (
            "go.opentelemetry.io/otel/trace", "1.41.0", ["GO-2027-0001"], [])]))
        status, out, _ = self.run_check(osv=report)
        self.assertEqual(status, 1, out)
        self.assertIn("NOT LISTED  GO-2027-0001", out)

    def test_an_advisory_listed_by_its_alias_is_covered(self):
        text = BASELINE.replace('id = "GO-2026-6974"', 'id = "CVE-2026-128085"')
        status, out, _ = self.run_check(text=text)
        self.assertEqual(status, 0, out)

    def test_an_entry_covers_until_the_day_before_its_date(self):
        status, out, _ = self.run_check(today="2026-12-27")
        self.assertEqual(status, 0, out)

    def test_an_entry_covers_nothing_from_its_date(self):
        status, out, _ = self.run_check(today="2026-12-28")
        self.assertEqual(status, 1, out)
        self.assertIn(f"PAST 2026-12-28  KSV-0112 (CRITICAL) {RBAC}: {WILDCARD}", out)
        self.assertIn("trivy: 4 reported, 1 covered by the baseline, 0 not listed, 3 past their date", out)
        self.assertIn("osv: 1 reported, 0 covered by the baseline, 0 not listed, 1 past their date", out)
        self.assertNotIn("KSV-0014", out)

    def test_a_listed_finding_that_is_gone_is_a_note_and_passes(self):
        status, out, _ = self.run_check(trivy=trivy_report(TRIVY_BASE[0]), osv=osv_report())
        self.assertEqual(status, 0, out)
        self.assertIn(f"note: listed and no longer reported, remove from the baseline (1x): "
                      f"KSV-0014 {DEPLOY}: {ROOTFS}", out)
        self.assertIn("(1x): GO-2026-6974", out)

    def test_a_passed_check_in_the_report_is_not_a_finding(self):
        report = trivy_report(*TRIVY_BASE, ("apps/prod/ok/deployment.yml",
                                            [misconfiguration("KSV-0014", ROOTFS, status="PASS")]))
        status, out, _ = self.run_check(trivy=report)
        self.assertEqual(status, 0, out)

    def test_a_result_with_another_status_is_a_finding(self):
        report = trivy_report(*TRIVY_BASE, ("apps/prod/odd/deployment.yml",
                                            [misconfiguration("KSV-0014", ROOTFS, status="EXCEPTION")]))
        status, out, _ = self.run_check(trivy=report)
        self.assertEqual(status, 1, out)

    def test_empty_reports_pass_with_notes(self):
        status, out, _ = self.run_check(trivy={"SchemaVersion": 2}, osv={"results": []})
        self.assertEqual(status, 0, out)
        self.assertIn("trivy: 0 reported", out)

    def test_unreadable_inputs_stop_with_status_2(self):
        cases = {
            "trivy report without SchemaVersion": {"trivy": {"Results": []}},
            "trivy report that is a list": {"trivy": []},
            "trivy finding without a message": {"trivy": {"SchemaVersion": 2, "Results": [
                {"Target": DEPLOY, "Misconfigurations": [{"ID": "KSV-0014", "Status": "FAIL"}]}]}},
            "osv report without results": {"osv": {}},
            "osv source outside the root": {"osv": {"results": [
                {"source": {"path": "/elsewhere/go.mod"}, "packages": []}]}},
            "osv vulnerabilities without groups": {"osv": {"results": [
                {"source": {"path": f"/repo/{GO_MOD}"}, "packages": [
                    {"package": {"name": "x", "version": "1"}, "vulnerabilities": [{"id": "GO-1"}]}]}]}},
            "baseline with an unknown table": {"text": BASELINE + '\n[[gitleaks]]\nid = "x"\n'},
            "baseline entry without a date": {"text": BASELINE.replace("expires = 2027-02-27\n", "")},
            "baseline date as a string": {"text": BASELINE.replace("2027-02-27", '"2027-02-27"')},
            "baseline entry with an extra key": {"text": BASELINE.replace(
                'statement = "Hardening backlog."', 'statement = "Hardening backlog."\npaths = ["apps"]')},
            "baseline with an empty statement": {"text": BASELINE.replace("Hardening backlog.", " ")},
            "baseline findings as a list": {"text": BASELINE.replace(
                f'[osv.findings]\n"{GO_MOD}" = ["go.opentelemetry.io/otel/trace@1.41.0"]',
                'findings = ["go.opentelemetry.io/otel/trace@1.41.0"]')},
            "baseline finding list that is empty": {"text": BASELINE.replace(
                '["go.opentelemetry.io/otel/trace@1.41.0"]', "[]")},
            "baseline that is not TOML": {"text": "[[trivy]\n"},
        }
        for name, arguments in cases.items():
            with self.subTest(name):
                status, out, err = self.run_check(**arguments)
                self.assertEqual(status, 2, out + err)
                self.assertIn("error:", err)

    def test_missing_files_stop_with_status_2(self):
        status, _, err = self.run_check(write=False)
        self.assertEqual(status, 2, err)

    def test_print_findings_writes_tables_the_baseline_reader_accepts(self):
        (self.root / "trivy.json").write_text(json.dumps(trivy_report(*TRIVY_BASE)))
        (self.root / "osv.json").write_text(json.dumps(osv_report(*OSV_BASE)))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = baseline.main([
                "--baseline", "unused", "--trivy", str(self.root / "trivy.json"),
                "--osv", str(self.root / "osv.json"), "--osv-root", "/repo", "--print-findings"])
        self.assertEqual(status, 0)
        text = out.getvalue()
        for tool in ("trivy", "osv"):
            text = text.replace(f"[{tool}.findings]", (
                f'[[{tool}]]\nid = "ID"\nexpires = 2027-01-01\nstatement = "s"\n[{tool}.findings]'))
        (self.root / "printed.toml").write_text(text)
        entries = baseline.read_baseline(str(self.root / "printed.toml"))
        self.assertEqual(sum(sum(e.findings.values()) for e in entries["trivy"]), 4)
        self.assertEqual(sum(sum(e.findings.values()) for e in entries["osv"]), 1)


if __name__ == "__main__":
    unittest.main()
