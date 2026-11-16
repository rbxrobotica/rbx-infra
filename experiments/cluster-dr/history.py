#!/usr/bin/env python3
"""Bounded aggregate Prometheus observations through read-only API proxy GETs."""

import datetime
import json
import math
import urllib.parse

import observe


PREFIX = "/api/v1/namespaces/monitoring/services/http:kube-prometheus-stack-prometheus:9090/proxy"
FILTER = 'job="kubelet",metrics_path="/metrics/cadvisor",container!="",container!="POD",namespace!=""'
MEM = "sum by(namespace) (container_memory_working_set_bytes{" + FILTER + "})"
CPU = "sum by(namespace) (rate(container_cpu_usage_seconds_total{" + FILTER + "}[5m]))"
QUERIES = {
    "container_series_by_node": "count by(node) (container_memory_working_set_bytes{" + FILTER + "})",
    "kubelet_scrape_up": 'sum by(node,metrics_path) (up{job="kubelet"})',
    "memory_now": MEM,
    "memory_7d_hourly_max": "max_over_time((" + MEM + ")[7d:1h])",
    "memory_7d_hourly_p95": "quantile_over_time(0.95,(" + MEM + ")[7d:1h])",
    "memory_7d_hourly_points": "count_over_time((" + MEM + ")[7d:1h])",
    "cpu_7d_hourly_max": "max_over_time((" + CPU + ")[7d:1h])",
    "cpu_7d_hourly_p95": "quantile_over_time(0.95,(" + CPU + ")[7d:1h])",
    "pvc_used_bytes": "max by(namespace,persistentvolumeclaim) (kubelet_volume_stats_used_bytes)",
    "pvc_capacity_bytes": "max by(namespace,persistentvolumeclaim) (kubelet_volume_stats_capacity_bytes)",
}


def get(path):
    return json.loads(observe.execute(["get", "--raw", PREFIX + path]))


def vector(data):
    if data.get("status") != "success" or data.get("data", {}).get("resultType") != "vector":
        raise observe.ObservationError("query_not_successful_vector")
    rows = data["data"]["result"]
    if len(rows) > 1000:
        raise observe.ObservationError("series_bound")
    result = []
    for row in rows:
        stamp, value = row["value"]
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not math.isfinite(stamp):
            raise observe.ObservationError("invalid_query_timestamp")
        value = float(value)
        if not math.isfinite(value):
            raise observe.ObservationError("nonfinite_query_result")
        result.append({"labels": {k: v for k, v in row.get("metric", {}).items()
                                  if k in ("namespace", "node", "metrics_path", "persistentvolumeclaim")},
                       "evaluated_epoch": stamp, "value": value})
    return result


def main():
    if observe.execute(["get", "namespace", "kube-system", "-o", "jsonpath={.metadata.uid}"]).strip() != observe.EXPECTED_UID:
        raise observe.ObservationError("cluster_identity_mismatch")
    report = {"type": "aggregate historical metadata, not a full-resolution SLO",
              "observed_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "lookback_days": 7, "subquery_step_seconds": 3600,
              "query_timeout_seconds": 5, "queries": {}, "gaps": {},
              "limitations": ["Hourly samples can miss short peaks",
                              "Missing targets/series are not zero load",
                              "Historical coverage must be compared with current inventory",
                              "Namespace observations do not size non-Kubernetes database processes"]}
    try:
        flags = get("/api/v1/status/flags")
        report["selected_flags"] = {k: v for k, v in flags.get("data", {}).items()
                                    if k in ("storage.tsdb.retention.time", "storage.tsdb.retention.size",
                                             "query.timeout", "query.max-concurrency")}
    except (observe.ObservationError, ValueError, KeyError, TypeError) as error:
        report["gaps"]["flags"] = type(error).__name__
    for label, query in QUERIES.items():
        path = "/api/v1/query?" + urllib.parse.urlencode({"query": query, "timeout": "5s"})
        try:
            report["queries"][label] = {"expression": query, "results": vector(get(path))}
        except (observe.ObservationError, ValueError, KeyError, TypeError) as error:
            report["gaps"][label] = str(error) if isinstance(error, observe.ObservationError) else type(error).__name__
    report["completed_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except observe.ObservationError as error:
        print(json.dumps({"completed": False, "error": str(error)}))
        raise SystemExit(1)
