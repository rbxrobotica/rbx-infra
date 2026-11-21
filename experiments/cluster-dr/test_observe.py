"""Offline privacy, identity and resource-accounting contracts for observe.py."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "cluster_dr_observe", Path(__file__).with_name("observe.py")
)
observe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(observe)


class ClusterObservationContracts(unittest.TestCase):
    def setUp(self):
        for method in ("Popen", "run"):
            guard = mock.patch.object(
                observe.subprocess, method,
                side_effect=AssertionError("real process or cluster access forbidden"),
            )
            guard.start()
            self.addCleanup(guard.stop)

    def test_wrong_cluster_identity_refuses_before_reading_workloads(self):
        output = io.StringIO()
        with mock.patch.object(observe, "execute", return_value="another-cluster-uid\n") as execute, \
                mock.patch.object(observe, "objects") as objects, \
                contextlib.redirect_stdout(output):
            with self.assertRaisesRegex(observe.ObservationError, "cluster_identity_mismatch"):
                observe.main()
        objects.assert_not_called()
        execute.assert_called_once_with(
            ["get", "namespace", "kube-system", "-o", "jsonpath={.metadata.uid}"]
        )
        self.assertEqual(output.getvalue(), "")

    def test_all_inventory_serializers_omit_payloads_and_secret_references(self):
        private = "SYNTHETIC_PRIVATE_VALUE_NEVER_REPORT"
        metadata = {
            "namespace": "fixture", "name": "safe-object",
            "annotations": {"kubectl.kubernetes.io/last-applied-configuration": private},
            "labels": {"private-label": private, "node-role.kubernetes.io/control-plane": private},
            "managedFields": [{"private": private}],
        }
        pod_spec = {
            "nodeName": "fixture-node",
            "containers": [{
                "name": "application", "image": "example.invalid/app:fixture",
                "resources": {"requests": {"cpu": "100m", "memory": "32Mi"}},
                "env": [{"name": "PASSWORD", "value": private}, {
                    "name": "TOKEN", "valueFrom": {"secretKeyRef": {"name": private, "key": private}}
                }],
                "envFrom": [{"secretRef": {"name": private}}],
                "command": ["private-command", private], "args": [private],
                "terminationMessagePath": private,
            }],
            "initContainers": [{
                "resources": {"requests": {"cpu": "10m", "memory": "8Mi"}},
                "env": [{"name": "INIT_SECRET", "value": private}], "args": [private],
            }],
            "imagePullSecrets": [{"name": private}],
            "volumes": [
                {"name": "secret", "secret": {"secretName": private}},
                {"name": "projected", "projected": {"sources": [{"secret": {"name": private}}]}},
                {"name": "data", "persistentVolumeClaim": {"claimName": "safe-data"}},
            ],
        }
        fixtures = [
            (observe.node, {"metadata": metadata, "spec": {"providerID": private}, "status": {
                "capacity": {"cpu": "2", "memory": "2Gi"},
                "allocatable": {"cpu": "1500m", "memory": "1Gi"},
                "addresses": [{"type": "InternalIP", "address": private}],
                "conditions": [{"type": "Ready", "status": "True", "message": private}],
                "nodeInfo": {"kubeletVersion": "v1.fixture", "systemUUID": private},
            }}),
            (observe.pod, {"metadata": metadata, "spec": pod_spec, "status": {
                "phase": "Running", "message": private,
                "conditions": [{"type": "Ready", "status": "True", "message": private}],
                "containerStatuses": [{"state": {"waiting": {"reason": "Pending", "message": private}}}],
            }}),
            (observe.workload, {"kind": "Deployment", "metadata": metadata, "spec": {
                "replicas": 2, "template": {"metadata": metadata, "spec": pod_spec},
            }}),
            (observe.workload, {"kind": "CronJob", "metadata": metadata, "spec": {
                "suspend": True, "schedule": "0 1 * * *",
                "jobTemplate": {"spec": {"template": {"metadata": metadata, "spec": pod_spec}}},
            }}),
            (observe.application, {"metadata": metadata, "spec": {
                "source": {"path": "apps/prod/fixture", "repoURL": private,
                           "helm": {"values": private, "parameters": [{"name": "secret", "value": private}]}},
                "destination": {"namespace": "fixture", "server": private},
            }, "status": {"health": {"status": "Healthy", "message": private},
                          "sync": {"status": "Synced", "revision": private}}}),
            (observe.pv, {"metadata": metadata, "spec": {
                "capacity": {"storage": "2Gi"},
                "local": {"path": private}, "hostPath": {"path": private},
                "csi": {"volumeHandle": private, "volumeAttributes": {"payload": private},
                        "nodePublishSecretRef": {"name": private, "namespace": private}},
                "claimRef": {"name": "safe-data", "namespace": "fixture", "uid": private},
            }}),
            (observe.pvc, {"metadata": metadata, "spec": {
                "resources": {"requests": {"storage": "2Gi"}},
                "dataSourceRef": {"name": private}, "volumeName": "safe-volume",
            }}),
        ]
        for serialize, item in fixtures:
            with self.subTest(serializer=serialize.__name__, kind=item.get("kind")):
                result = json.dumps(serialize(item), sort_keys=True)
                self.assertNotIn(private, result)
                for forbidden in ("secretKeyRef", "imagePullSecrets", "envFrom", "managedFields",
                                  "annotations", "repoURL", "volumeAttributes", "command"):
                    self.assertNotIn('"' + forbidden + '"', result)

    def test_effective_requests_include_overlapping_sidecars_init_peak_and_overhead(self):
        def container(cpu, memory, *, sidecar=False):
            result = {"resources": {"requests": {"cpu": cpu, "memory": memory}}}
            if sidecar:
                result["restartPolicy"] = "Always"
            return result

        spec = {
            "containers": [container("100m", "100Mi"), container("200m", "200Mi")],
            "initContainers": [
                container("700m", "700Mi"), container("100m", "50Mi", sidecar=True),
                container("600m", "800Mi"), container("200m", "100Mi", sidecar=True),
                container("50m", "50Mi"),
            ],
            "overhead": {"cpu": "50m", "memory": "25Mi"},
        }
        # CPU init peak: 700m; memory init peak: 50Mi + 800Mi.
        # Steady sidecars add 300m/150Mi, rather than vanishing after init.
        self.assertEqual(observe.requests(spec), {"cpu_m": 750.0, "memory_bytes": 875 * 1024**2})
        spec["containers"] = [container("2", "1Mi")]
        self.assertEqual(observe.requests(spec), {"cpu_m": 2350.0, "memory_bytes": 875 * 1024**2})

    def test_unmodelled_pod_resources_and_invalid_quantities_are_not_reported_as_zero(self):
        for spec in (
            {"containers": [], "resources": {"requests": {"cpu": "2", "memory": "1Gi"}}},
            {"containers": [{"resources": {"requests": {"cpu": "PRIVATE_INVALID_QUANTITY"}}}]},
        ):
            with self.subTest(spec=spec):
                report = observe.requests(spec)
                self.assertEqual(report, {"unsupported_resource_model": True})
                self.assertNotIn("PRIVATE_INVALID_QUANTITY", json.dumps(report))

    def test_unmodelled_resource_claims_or_opaque_fields_do_not_leak_or_become_zero(self):
        private = "SYNTHETIC_PRIVATE_RESOURCE_DETAIL"
        for spec in (
            {"containers": [{"resources": {
                "requests": {"cpu": "100m", "memory": "32Mi"},
                "claims": [{"name": private}],
            }}]},
            {"containers": [{"resources": {"requests": {"memory": "32Mi"}}}],
             "overhead": {"memory": private}},
            {"containers": [], "resources": {"requests": {"memory": "1Gi"},
                                                 "opaque": {"payload": private}}},
        ):
            with self.subTest(spec=spec):
                result = observe.pod({"metadata": {"name": "fixture"}, "spec": spec})
                self.assertEqual(result["requests"], {"unsupported_resource_model": True})
                self.assertNotIn(private, json.dumps(result))

    def test_failed_reads_produce_gaps_instead_of_zero_or_empty_success(self):
        def read_objects(resource):
            if resource == "pods":
                raise observe.ObservationError("forbidden")
            if resource == "pods.metrics.k8s.io":
                raise json.JSONDecodeError("PRIVATE_API_DETAIL", "PRIVATE_RESPONSE", 0)
            return []

        output = io.StringIO()
        with mock.patch.object(observe, "execute", return_value=observe.EXPECTED_UID), \
                mock.patch.object(observe, "objects", side_effect=read_objects), \
                contextlib.redirect_stdout(output):
            observe.main()
        report = json.loads(output.getvalue())
        self.assertTrue(report["identity_verified"])
        self.assertEqual(report["gaps"]["pods"], "forbidden")
        self.assertEqual(report["gaps"]["pods.metrics.k8s.io"], "JSONDecodeError")
        self.assertNotIn("pods", report["observations"])
        self.assertNotIn("pods.metrics.k8s.io", report["observations"])
        self.assertEqual(report["observations"]["nodes"], [])
        self.assertNotIn("PRIVATE_", output.getvalue())

    def test_complete_metrics_support_unit_conversion_aggregation_and_explicit_zero(self):
        result = observe.metrics({
            "metadata": {"namespace": "fixture", "name": "pod"},
            "containers": [
                {"name": "a", "usage": {"cpu": "100000000n", "memory": "32Mi"}},
                {"name": "b", "usage": {"cpu": "25000u", "memory": "8Mi"}},
            ],
        })
        self.assertEqual(result["cpu_m"], 125.0)
        self.assertEqual(result["memory_bytes"], 40 * 1024**2)
        explicit_zero = observe.metrics({"usage": {"cpu": "0", "memory": "0"}})
        self.assertEqual(explicit_zero["cpu_m"], 0)
        self.assertEqual(explicit_zero["memory_bytes"], 0)

    def test_partial_or_missing_metrics_fail_instead_of_fabricating_zero_usage(self):
        for item in (
            {}, {"usage": {"cpu": "10m"}},
            {"containers": [{"name": "a", "usage": {"memory": "32Mi"}}]},
        ):
            with self.subTest(item=item):
                with self.assertRaises((ValueError, KeyError, observe.ObservationError)):
                    observe.metrics(item)


if __name__ == "__main__":
    unittest.main()
