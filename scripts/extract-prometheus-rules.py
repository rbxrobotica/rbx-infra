#!/usr/bin/env python3
"""Extract the Prometheus rule files from the kube-prometheus-stack Application.

The rules live in a Helm values string (spec.source.helm.values), under
additionalPrometheusRulesMap. yamllint and kubeconform see that string as one
scalar, so this writes each map entry to <out-dir>/<key>.yml as a Prometheus
rule file for promtool. Run through scripts/test-prometheus-rules.sh.

Usage: python3 extract-prometheus-rules.py <kube-prometheus-stack.yml> <out-dir>
"""
from pathlib import Path
import sys
import yaml


def main():
    manifest, out = Path(sys.argv[1]), Path(sys.argv[2])
    application = yaml.safe_load(manifest.read_text())
    values = yaml.safe_load(application["spec"]["source"]["helm"]["values"])
    rules_map = values.get("additionalPrometheusRulesMap")
    if not rules_map:
        raise SystemExit(f"{manifest}: no additionalPrometheusRulesMap in the Helm values")
    out.mkdir(parents=True, exist_ok=True)
    for key, entry in rules_map.items():
        groups = entry["groups"]
        alerts = sum(1 for group in groups for rule in group["rules"] if "alert" in rule)
        (out / f"{key}.yml").write_text(yaml.safe_dump({"groups": groups}, sort_keys=False, width=1000))
        print(f"{key}: groups {[group['name'] for group in groups]}, {alerts} alerts")


if __name__ == "__main__":
    main()
