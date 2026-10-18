import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("capacity", Path(__file__).parents[1] / "check-node-reliability-capacity.py")
capacity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capacity)


def container(memory, **kwargs):
    return {"resources": {"requests": {"memory": memory}}, **kwargs}


class ResourceAccountingTests(unittest.TestCase):
    def test_binary_and_decimal_memory_are_distinct(self):
        self.assertEqual(capacity.quantity("1Gi"), 1024 ** 3)
        self.assertEqual(capacity.quantity("1G"), 1000 ** 3)
        self.assertLess(capacity.quantity("400m"), 1)

    def test_cpu_subcore_values_do_not_round_down(self):
        self.assertEqual(capacity.quantity("100m"), capacity.quantity("0.1"))
        self.assertEqual(capacity.quantity("100000000n"), capacity.quantity("100m"))

    def test_unsupported_value_blocks_a_restart(self):
        for value in ("", "1GiB", "-1Gi", "not-a-quantity"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                capacity.quantity(value)

    def test_init_peak_can_exceed_steady_state(self):
        pod = {"spec": {"containers": [container("128Mi")], "initContainers": [container("1Gi"), container("256Mi")]}}
        self.assertEqual(capacity.pod_request(pod, "memory"), capacity.quantity("1Gi"))

    def test_native_sidecar_remains_concurrent(self):
        pod = {"spec": {"containers": [container("256Mi")], "initContainers": [container("64Mi", restartPolicy="Always"), container("512Mi")], "overhead": {"memory": "16Mi"}}}
        self.assertEqual(capacity.pod_request(pod, "memory"), capacity.quantity("592Mi"))

    def test_sidecar_is_added_to_regular_containers(self):
        pod = {"spec": {"containers": [container("1Gi")], "initContainers": [container("64Mi", restartPolicy="Always"), container("128Mi")]}}
        self.assertEqual(capacity.pod_request(pod, "memory"), capacity.quantity("1088Mi"))

    def test_unknown_pod_budget_never_silently_passes(self):
        with self.assertRaises(ValueError):
            capacity.pod_request({"spec": {"resources": {"requests": {"memory": "1Gi"}}, "containers": []}}, "memory")

    def test_two_ready_pods_on_one_host_are_not_redundant(self):
        pod = {"metadata": {"labels": {"k8s-app": "kube-dns"}}, "spec": {"nodeName": "tiger"}, "status": {"conditions": [{"type": "Ready", "status": "True"}]}}
        self.assertEqual(capacity.critical_service_hosts([pod, pod], "kube-dns"), {"tiger"})

    def test_terminating_or_unready_pod_cannot_justify_restart(self):
        terminating = {"metadata": {"labels": {"app.kubernetes.io/name": "traefik"}, "deletionTimestamp": "2026-10-18T00:00:00Z"}, "spec": {"nodeName": "sumatrae"}, "status": {"conditions": [{"type": "Ready", "status": "True"}]}}
        unready = {"metadata": {"labels": {"app.kubernetes.io/name": "traefik"}}, "spec": {"nodeName": "tiger"}, "status": {"conditions": [{"type": "Ready", "status": "False"}]}}
        self.assertEqual(capacity.critical_service_hosts([terminating, unready], "traefik"), set())

    def test_reservations_require_the_specific_scheduling_barrier(self):
        barrier = {"key": "rbx.io/maintenance", "value": "stabilization", "effect": "NoSchedule"}
        self.assertTrue(capacity.maintenance_barrier({"spec": {"taints": [barrier]}}))
        for spec in ({}, {"taints": [{**barrier, "effect": "PreferNoSchedule"}]}, {"taints": [{**barrier, "value": "unreviewed"}]}, {"taints": [barrier], "unschedulable": True}):
            with self.subTest(spec=spec):
                self.assertFalse(capacity.maintenance_barrier({"spec": spec}))


if __name__ == "__main__":
    unittest.main()
