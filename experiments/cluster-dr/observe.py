#!/usr/bin/env python3
"""Read-only, identity-pinned cluster metadata and capacity observation."""

import datetime
import importlib.util
import json
from pathlib import Path
import re
import selectors
import subprocess
import time


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "capacity_guard", ROOT / "scripts/check-node-reliability-capacity.py")
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)
EXPECTED_UID = "7c54362a-7c64-4d62-a6ec-a14d4843e8a5"
MAX_BYTES = 16 * 1024 * 1024
BASE = ["kubectl", "--server=https://158.220.116.31:6443", "--request-timeout=12s"]


class ObservationError(Exception):
    pass


def execute(args):
    """Do not echo command stderr, API payloads or credential-plugin output."""
    process = subprocess.Popen(BASE + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    streams = selectors.DefaultSelector()
    streams.register(process.stdout, selectors.EVENT_READ, "out")
    streams.register(process.stderr, selectors.EVENT_READ, "err")
    buffers = {"out": bytearray(), "err": bytearray()}
    deadline = time.monotonic() + 30
    try:
        while streams.get_map():
            if time.monotonic() > deadline:
                raise ObservationError("timeout")
            for key, _ in streams.select(timeout=0.25):
                chunk = key.fileobj.read1(65536)
                if not chunk:
                    streams.unregister(key.fileobj)
                    continue
                buffer = buffers[key.data]
                buffer.extend(chunk)
                if len(buffer) > (MAX_BYTES if key.data == "out" else 16384):
                    raise ObservationError("output_bound")
        process.wait(timeout=2)
        if process.returncode:
            error = buffers["err"].decode("utf-8", errors="replace").lower()
            raise ObservationError("forbidden" if "forbidden" in error else
                                   "unauthorized" if "unauthorized" in error else "read_failed")
        return buffers["out"].decode("utf-8")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        streams.close()
        process.stdout.close()
        process.stderr.close()


def objects(resource, namespace=None):
    args = ["get", resource, "-o", "json", "--chunk-size=100"]
    args += ["-n", namespace] if namespace else ["-A"]
    value = json.loads(execute(args))
    rows = value.get("items")
    if not isinstance(rows, list) or len(rows) > 5000:
        raise ObservationError("object_bound_or_shape")
    return rows


def name(item):
    metadata = item.get("metadata", {})
    return {"namespace": metadata.get("namespace"), "name": metadata.get("name"),
            "uid": metadata.get("uid")}


def resource_spec(spec):
    """Only resource fields; never retain Env, command, annotations or payloads."""
    output = {}
    def clean(mapping):
        if not isinstance(mapping, dict):
            raise ValueError("invalid_resource_mapping")
        result = {}
        for key, value in mapping.items():
            if key not in ("cpu", "memory", "ephemeral-storage") and not re.fullmatch(
                    r"hugepages-[0-9]+(?:Ki|Mi|Gi)", key):
                raise ValueError("unsupported_resource_name")
            GUARD.quantity(value)
            result[key] = value
        return result
    for kind in ("containers", "initContainers"):
        output[kind] = []
        for c in spec.get(kind, []):
            resources = c.get("resources", {})
            safe = {}
            try:
                if not isinstance(resources, dict) or set(resources) - {"requests", "limits"}:
                    raise ValueError("unsupported_resource_fields")
                safe["resources"] = {key: clean(value) for key, value in resources.items()}
            except (ValueError, TypeError):
                safe = {"resource_schema_gap": True}
            if c.get("restartPolicy") == "Always":
                safe["restartPolicy"] = "Always"
            output[kind].append(safe)
    for key in ("overhead", "resources"):
        if key in spec:
            try:
                output[key] = (clean(spec[key]) if key == "overhead" else
                               {k: clean(v) for k, v in spec[key].items() if k in ("requests", "limits")})
                if key == "resources" and set(spec[key]) - {"requests", "limits"}:
                    raise ValueError("unsupported_pod_resource_fields")
            except (ValueError, TypeError, AttributeError):
                output["resource_schema_gap"] = True
    return output


def requests(spec):
    try:
        pod = {"spec": resource_spec(spec)}
        if pod["spec"].get("resource_schema_gap") or any(
                c.get("resource_schema_gap") for group in ("containers", "initContainers")
                for c in pod["spec"][group]):
            raise ValueError("resource_schema_gap")
        return {"cpu_m": float(GUARD.pod_request(pod, "cpu") * 1000),
                "memory_bytes": int(GUARD.pod_request(pod, "memory"))}
    except (ValueError, KeyError, TypeError):
        return {"unsupported_resource_model": True}


def claims(spec):
    return [v["persistentVolumeClaim"]["claimName"] for v in spec.get("volumes", [])
            if "persistentVolumeClaim" in v]


def pod(item):
    spec, status = item.get("spec", {}), item.get("status", {})
    containers = status.get("containerStatuses", [])
    return {**name(item), "node": spec.get("nodeName"), "phase": status.get("phase"),
            "deleting": bool(item.get("metadata", {}).get("deletionTimestamp")),
            "ready": any(c.get("type") == "Ready" and c.get("status") == "True"
                         for c in status.get("conditions", [])),
            "requests": requests(spec), "container_resources": resource_spec(spec),
            "unrequested_memory_containers": sum(
                "memory" not in c.get("resources", {}).get("requests", {})
                for c in spec.get("containers", [])),
            "owners": [{"kind": o.get("kind"), "name": o.get("name")}
                       for o in item.get("metadata", {}).get("ownerReferences", [])],
            "pvc_claims": claims(spec),
            "restarts": sum(c.get("restartCount", 0) for c in containers),
            "waiting_reasons": [c["state"]["waiting"].get("reason") for c in containers
                                if "waiting" in c.get("state", {})],
            "last_termination_reasons": [c["lastState"]["terminated"].get("reason") for c in containers
                                         if "terminated" in c.get("lastState", {})],
            "images": [c.get("image") for c in spec.get("containers", [])]}


def node(item):
    status = item.get("status", {})
    return {**name(item), "capacity": status.get("capacity"),
            "allocatable": status.get("allocatable"),
            "kubelet_version": status.get("nodeInfo", {}).get("kubeletVersion"),
            "ready": any(c.get("type") == "Ready" and c.get("status") == "True"
                         for c in status.get("conditions", [])),
            "roles": [k for k in item.get("metadata", {}).get("labels", {})
                      if k.startswith("node-role.kubernetes.io/")],
            "taints": [{k: t.get(k) for k in ("key", "value", "effect")}
                       for t in item.get("spec", {}).get("taints", [])],
            "unschedulable": item.get("spec", {}).get("unschedulable", False)}


def workload(item):
    kind, spec, status = item.get("kind"), item.get("spec", {}), item.get("status", {})
    template = spec.get("template", {}).get("spec", {})
    if kind == "CronJob":
        template = spec.get("jobTemplate", {}).get("spec", {}).get("template", {}).get("spec", {})
    return {**name(item), "kind": kind, "replicas": spec.get("replicas"),
            "ready_replicas": status.get("readyReplicas", status.get("numberReady")),
            "desired_scheduled": status.get("desiredNumberScheduled"),
            "suspended": spec.get("suspend"), "schedule": spec.get("schedule"),
            "strategy": spec.get("strategy", spec.get("updateStrategy", {})).get("type"),
            "requests_per_pod": requests(template), "pvc_claims": claims(template),
            "volume_claim_templates": [{"name": c.get("metadata", {}).get("name"),
                                        "storage": c.get("spec", {}).get("resources", {}).get("requests", {}).get("storage")}
                                       for c in spec.get("volumeClaimTemplates", [])],
            "active_jobs": len(status.get("active", [])) if isinstance(status.get("active"), list)
                           else status.get("active"),
            "last_schedule_time": status.get("lastScheduleTime")}


def pvc(item):
    spec, status = item.get("spec", {}), item.get("status", {})
    return {**name(item), "phase": status.get("phase"), "class": spec.get("storageClassName"),
            "requested": spec.get("resources", {}).get("requests", {}).get("storage"),
            "capacity": status.get("capacity", {}).get("storage"), "pv": spec.get("volumeName")}


def pv(item):
    spec = item.get("spec", {})
    nodes = []
    for term in spec.get("nodeAffinity", {}).get("required", {}).get("nodeSelectorTerms", []):
        for rule in term.get("matchExpressions", []):
            if rule.get("key") == "kubernetes.io/hostname":
                nodes.extend(rule.get("values", []))
    ref = spec.get("claimRef", {})
    return {**name(item), "capacity": spec.get("capacity", {}).get("storage"),
            "class": spec.get("storageClassName"), "reclaim_policy": spec.get("persistentVolumeReclaimPolicy"),
            "volume_kind": next((k for k in ("local", "hostPath", "csi", "nfs", "awsElasticBlockStore") if k in spec), "other"),
            "nodes": nodes, "claim": {"namespace": ref.get("namespace"), "name": ref.get("name")}}


def application(item):
    spec, status = item.get("spec", {}), item.get("status", {})
    sources = spec.get("sources", [spec.get("source", {})])
    auto = spec.get("syncPolicy", {}).get("automated")
    return {**name(item), "health": status.get("health", {}).get("status"),
            "sync": status.get("sync", {}).get("status"),
            "destination_namespace": spec.get("destination", {}).get("namespace"),
            "source_paths": [s.get("path") for s in sources if s.get("path")],
            "charts": [s.get("chart") for s in sources if s.get("chart")],
            "auto_sync": auto is not None,
            "auto_prune": bool((auto or {}).get("prune")),
            "auto_self_heal": bool((auto or {}).get("selfHeal"))}


def metrics(item):
    containers = item.get("containers", [])
    usage = item.get("usage", {})
    if containers:
        if any(not all(k in c.get("usage", {}) for k in ("cpu", "memory")) for c in containers):
            raise ObservationError("incomplete_metrics")
        usage = {k: sum((GUARD.quantity(c.get("usage", {}).get(k, "0")) for c in containers))
                 for k in ("cpu", "memory")}
    if not all(k in usage for k in ("cpu", "memory")):
        raise ObservationError("incomplete_metrics")
    return {**name(item), "timestamp": item.get("timestamp"), "window": item.get("window"),
            "cpu_m": float(GUARD.quantity(usage.get("cpu", "0")) * 1000),
            "memory_bytes": int(GUARD.quantity(usage.get("memory", "0")))}


def service(item):
    spec = item.get("spec", {})
    return {**name(item), "type": spec.get("type"),
            "external_name": spec.get("externalName"),
            "ports": [{"name": p.get("name"), "port": p.get("port"), "protocol": p.get("protocol")}
                      for p in spec.get("ports", [])]}


def main():
    identity = execute(["get", "namespace", "kube-system", "-o", "jsonpath={.metadata.uid}"]).strip()
    if identity != EXPECTED_UID:
        raise ObservationError("cluster_identity_mismatch")
    report = {"type": "read-only cluster DR capacity metadata", "identity_verified": True,
              "observed_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "secret_configmap_or_log_reads": False, "cluster_changes": False,
              "observations": {}, "gaps": {}}
    readers = [("nodes", node), ("pods", pod), ("deployments.apps", workload),
               ("statefulsets.apps", workload), ("daemonsets.apps", workload),
               ("cronjobs.batch", workload), ("jobs.batch", workload),
               ("persistentvolumeclaims", pvc), ("persistentvolumes", pv),
               ("applications.argoproj.io", application),
               ("nodes.metrics.k8s.io", metrics), ("pods.metrics.k8s.io", metrics),
               ("services", service)]
    for resource, summarize in readers:
        try:
            report["observations"][resource] = [summarize(x) for x in objects(resource)]
        except (ObservationError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
            report["gaps"][resource] = (str(error) if isinstance(error, ObservationError)
                                        else type(error).__name__)
    if execute(["get", "namespace", "kube-system", "-o", "jsonpath={.metadata.uid}"]).strip() != EXPECTED_UID:
        raise ObservationError("cluster_identity_changed")
    report["completed_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except ObservationError as error:
        print(json.dumps({"completed": False, "error": str(error)}))
        raise SystemExit(1)
