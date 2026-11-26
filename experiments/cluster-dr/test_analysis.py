"""Offline accounting regressions: missing observations are never proof of fit."""
import unittest

import analyze


class WorksheetContracts(unittest.TestCase):
    def fixture(self):
        def pod(name, phase="Running", request=None):
            return {"namespace": "rbx-identity", "name": name, "phase": phase,
                    "ready": True, "unrequested_memory_containers": 0,
                    "requests": request or {"cpu_m": 100, "memory_bytes": analyze.GIB}}
        return {"identity_verified": True, "observed_at_utc": "fixture", "observations": {
            "pods": [pod("active"), pod("completed", "Succeeded"), pod("no-metric")],
            "pods.metrics.k8s.io": [{"namespace": "rbx-identity", "name": "active",
                                     "memory_bytes": analyze.GIB}],
        }}

    def test_completed_pods_do_not_inflate_requests_and_missing_metrics_stay_visible(self):
        report = analyze.derive(self.fixture(), {})
        row = report["namespaces"]["rbx-identity"]
        self.assertEqual(row["memory_requests_bytes"], 2 * analyze.GIB)
        self.assertEqual(row["metric_missing_pods"], 1)
        self.assertEqual(report["missing_metric_pods"], ["rbx-identity/no-metric"])
        self.assertIsNone(row["hourly_sampled_memory_max_bytes"])

    def test_hourly_envelope_and_allowances_are_explicit_not_a_fit_verdict(self):
        history = {"queries": {"memory_7d_hourly_max": {"results": [
            {"labels": {"namespace": "rbx-identity"}, "value": 3 * analyze.GIB}]}}}
        report = analyze.derive(self.fixture(), history)
        profile = report["profiles"]["selected_core_candidate"]
        self.assertEqual(profile["namespace_envelope_sum_gib"], 3)
        self.assertAlmostEqual(profile["proposed_compute_budget_gib"], 6.55)
        self.assertNotIn("fits", profile)

    def test_unsupported_resources_cannot_silently_become_known_zero(self):
        fixture = self.fixture()
        fixture["observations"]["pods"][0]["requests"] = {"unsupported_resource_model": True}
        row = analyze.derive(fixture, {})["namespaces"]["rbx-identity"]
        self.assertEqual(row["unsupported_resource_pods"], 1)
        self.assertEqual(row["memory_requests_bytes"], analyze.GIB)

    def test_unverified_identity_and_duplicate_metrics_refuse(self):
        fixture = self.fixture()
        fixture["identity_verified"] = False
        with self.assertRaisesRegex(ValueError, "identity_unverified"):
            analyze.derive(fixture, {})
        fixture["identity_verified"] = True
        fixture["observations"]["pods.metrics.k8s.io"] *= 2
        with self.assertRaisesRegex(ValueError, "duplicate_pod_metric"):
            analyze.derive(fixture, {})


if __name__ == "__main__":
    unittest.main()
