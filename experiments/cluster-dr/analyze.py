#!/usr/bin/env python3
"""Offline worksheet over sanitized receipts; never connects to infrastructure."""

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path


GIB = 2 ** 30
CORE = {
    "argocd", "cert-manager", "external-secrets-system", "kube-system",
    "rbx-identity", "rbx-commerce", "rbx-comms", "rbx-portal", "rbx-catalog",
    "rbx-console", "rbxsystems-ch", "rbx-ia-br", "kulinaryos", "merovelis",
    "md-prec-kulinaryos",
}
EXPANDED = CORE | {
    "eden", "rbx-eden-system-creator", "rbx-cms", "rbx-flightdeck",
    "rbx-maestro", "rbx-market-graph", "rbx-data", "rbx-memory",
    "strategos", "rbx-atlas", "truthmetal",
    "rbx-briefing-btc", "rbx-landing-briefing-btc",
}


def derive(cluster, history):
    if not cluster.get("identity_verified"):
        raise ValueError("identity_unverified")
    data = cluster["observations"]
    active = [p for p in data["pods"] if p["phase"] not in ("Succeeded", "Failed")]
    active_names = {(p["namespace"], p["name"]) for p in active}
    ns = defaultdict(lambda: {
        "active_pods": 0, "ready_pods": 0, "memory_requests_bytes": 0,
        "cpu_requests_m": 0, "containers_without_memory_requests": 0,
        "metric_pods": 0, "metric_memory_bytes": 0,
        "unsupported_resource_pods": 0,
    })
    for p in active:
        row = ns[p["namespace"]]
        row["active_pods"] += 1
        row["ready_pods"] += bool(p["ready"])
        row["containers_without_memory_requests"] += p["unrequested_memory_containers"]
        if p["requests"].get("unsupported_resource_model"):
            row["unsupported_resource_pods"] += 1
        else:
            row["memory_requests_bytes"] += p["requests"]["memory_bytes"]
            row["cpu_requests_m"] += p["requests"]["cpu_m"]
    metric_names = set()
    for m in data.get("pods.metrics.k8s.io", []):
        name = m["namespace"], m["name"]
        if name not in active_names:
            continue
        if name in metric_names:
            raise ValueError("duplicate_pod_metric")
        metric_names.add(name)
        ns[m["namespace"]]["metric_pods"] += 1
        ns[m["namespace"]]["metric_memory_bytes"] += m["memory_bytes"]
    historical = {}
    points = {}
    for key, mapping in (("memory_7d_hourly_max", historical),
                         ("memory_7d_hourly_points", points)):
        for r in history.get("queries", {}).get(key, {}).get("results", []):
            mapping[r["labels"]["namespace"]] = r["value"]
    for name, row in ns.items():
        row["metric_missing_pods"] = row["active_pods"] - row["metric_pods"]
        row["hourly_sampled_memory_max_bytes"] = historical.get(name)
        row["hourly_memory_points"] = points.get(name)
        row["planning_envelope_bytes"] = max(
            row["memory_requests_bytes"], row["metric_memory_bytes"], historical.get(name, 0))
    profiles = {}
    for label, selected in (("selected_core_candidate", CORE),
                            ("expanded_core_candidate", EXPANDED),
                            ("observed_active_fleet_not_required_scope", set(ns))):
        rows = [ns[n] for n in selected if n in ns]
        envelope = sum(r["planning_envelope_bytes"] for r in rows)
        profiles[label] = {
            "namespaces": sorted(selected), "missing_namespaces": sorted(selected - set(ns)),
            "active_pods": sum(r["active_pods"] for r in rows),
            "cpu_requests_cores": sum(r["cpu_requests_m"] for r in rows) / 1000,
            "memory_requests_gib": sum(r["memory_requests_bytes"] for r in rows) / GIB,
            "metric_memory_gib": sum(r["metric_memory_bytes"] for r in rows) / GIB,
            "namespace_envelope_sum_gib": envelope / GIB,
            "proposed_compute_budget_gib": ((envelope + .5 * GIB) * 1.3 + 2 * GIB) / GIB,
            "metric_missing_pods": sum(r["metric_missing_pods"] for r in rows),
            "unsupported_resource_pods": sum(r["unsupported_resource_pods"] for r in rows),
        }
    return {
        "type": "offline sizing hypothesis, not measured target acceptance",
        "source_observation_utc": cluster["observed_at_utc"],
        "collector_gaps": cluster.get("gaps", {}), "history_gaps": history.get("gaps", {}),
        "formula": "(sum_selected_namespace(max(requests, current_known_metrics, hourly_sampled_max)) + 0.5 GiB unknown-work allowance) * 1.30 + 2 GiB OS/k3s reserve",
        "limitations": [
            "Namespace peaks are not simultaneous; summation is a conservative planning heuristic",
            "Known request/metric sums are partial when unsupported or missing counts are nonzero",
            "The fixed allowances are proposed assumptions, not measured requirements",
            "Hourly samples miss short peaks; points do not prove continuous coverage",
            "Host count changes DaemonSets, control planes, allocatable, rollout and affinity constraints",
            "External DB and target agents/workspace are separate; selected cores defer full monitoring",
            "Expanded core excludes Thalamus/Observability functions with unselected LiteLLM/Langfuse backends",
            "Vendor GB, actual host memory and schedulable GiB must be reconciled in a pilot",
            "No business priority, recovery order or service-level target is established by this worksheet",
        ],
        "counts": {k: len(v) for k, v in data.items()},
        "active_pods": len(active), "ready_active_pods": sum(p["ready"] for p in active),
        "missing_metric_pods": sorted("/".join(n) for n in active_names - metric_names),
        "namespaces": dict(sorted(ns.items())), "profiles": profiles,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipts", type=Path)
    args = parser.parse_args()
    names = ("cluster-observation-2.json", "prometheus-history-2.json")
    raw = [(args.receipts / name).read_bytes() for name in names]
    result = derive(*(json.loads(value) for value in raw))
    result["input_sha256"] = {n: hashlib.sha256(v).hexdigest() for n, v in zip(names, raw)}
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
