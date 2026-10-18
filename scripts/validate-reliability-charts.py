#!/usr/bin/env python3
"""Render the actual ArgoCD values and guard existing storage and identity."""
import argparse
from pathlib import Path
import subprocess
import tempfile
import yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--helm", default="helm")
    parser.add_argument("--charts-dir", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sources = [
        ("argocd", "platform/argocd/application.yml", "argocd", "argocd"),
        ("prometheus", "platform/monitoring/kube-prometheus-stack.yml", "kube-prometheus-stack", "monitoring"),
        ("loki", "platform/monitoring/loki.yml", "loki", "monitoring"),
    ]
    with tempfile.TemporaryDirectory(prefix="rbx-chart-contract-") as temp:
        scratch = Path(temp)
        for name, file, release, namespace in sources:
            source = yaml.safe_load((root / file).read_text())["spec"]["source"]
            helm = source["helm"]
            if helm.get("values", "").strip() and helm.get("valuesObject") is not None:
                raise SystemExit(f"{file}: ambiguous inline values")
            # ArgoCD chooses one inline field; two -f arguments would hide the bug.
            values = yaml.safe_dump(helm["valuesObject"]) if helm.get("valuesObject") is not None else helm.get("values", "{}")
            values_file = scratch / (name + "-values.yml")
            values_file.write_text(values)
            if args.charts_dir:
                chart = args.charts_dir / (name + ".tgz")
            else:
                subprocess.run([args.helm, "pull", source["chart"], "--repo", source["repoURL"], "--version", source["targetRevision"], "--destination", temp], check=True, timeout=90)
                chart = scratch / (source["chart"] + "-" + source["targetRevision"] + ".tgz")
            result = subprocess.run([args.helm, "template", release, str(chart), "--namespace", namespace, "--kube-version", "1.36.2", "-f", str(values_file)], check=True, capture_output=True, text=True, timeout=60)
            docs = [d for d in yaml.safe_load_all(result.stdout) if isinstance(d, dict)]

            def find(kind, resource):
                return next(d for d in docs if d["kind"] == kind and d["metadata"]["name"] == resource)

            if name == "argocd":
                pod = find("StatefulSet", "argocd-application-controller")["spec"]["template"]["spec"]
                assert pod["containers"][0]["image"].endswith(":v3.4.7"), "ArgoCD image pin lost"
                assert find("ConfigMap", "argocd-cm")["data"]["application.resourceTrackingMethod"] == "label", "resource tracking changed"
                assert not any(d["kind"] == "Deployment" and d["metadata"]["name"] == "argocd-notifications-controller" for d in docs), "disabled component re-enabled"
            elif name == "prometheus":
                assert find("PersistentVolumeClaim", "kube-prometheus-stack-grafana")["spec"]["resources"]["requests"]["storage"] == "5Gi", "Grafana persistence lost"
                for kind, resource, size in [("Prometheus", "kube-prometheus-stack-prometheus", "10Gi"), ("Alertmanager", "kube-prometheus-stack-alertmanager", "2Gi")]:
                    assert find(kind, resource)["spec"]["storage"]["volumeClaimTemplate"]["spec"]["resources"]["requests"]["storage"] == size, "monitoring persistence lost"
                assert not any(d["kind"] == "ValidatingWebhookConfiguration" for d in docs), "disabled admission webhook re-enabled"
            else:
                claim = find("StatefulSet", "loki")["spec"]["volumeClaimTemplates"][0]
                assert claim["spec"]["resources"]["requests"]["storage"] == "20Gi", "Loki persistence lost"
                assert not any(d["kind"] == "StatefulSet" and d["metadata"]["name"] in ("loki-read", "loki-write", "loki-backend") for d in docs), "Loki deployment mode changed"
            print(f"{name}: rendered effective ArgoCD values; storage and identity contracts passed")


if __name__ == "__main__":
    main()
