#!/usr/bin/env bash
# Check and unit-test the Prometheus rules in
# platform/monitoring/kube-prometheus-stack.yml with promtool.
#
# The rules live in a Helm values string, which yamllint and kubeconform see
# as one scalar. This extracts every additionalPrometheusRulesMap entry to
# rules/<key>.yml (scripts/extract-prometheus-rules.py), runs
# `promtool check rules` over all of them, then `promtool test rules` over
# scripts/tests/prometheus-rules/*.test.yml, whose rule_files name
# rules/<key>.yml.
#
# Runs the same way locally and in CI (validate.yml, job prometheus-rules):
#   bash scripts/test-prometheus-rules.sh
# Both steps run in podman, from images pinned by digest. promtool 3.0.1 is
# the Prometheus that kube-prometheus-stack 67.4.0, the chart version this repo
# deploys, ships: bump them together. PyYAML is pinned by hash in
# scripts/tests/prometheus-rules/requirements.txt. promtool runs offline.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_image="docker.io/library/python:3.12.16-alpine@sha256:e064671ca452259bab50e8e2c7bd896e39e9a343bb06ca25234adf6fe18d0fc7"
promtool_image="docker.io/prom/prometheus:v3.0.1@sha256:565ee86501224ebbb98fc10b332fa54440b100469924003359edf49cbce374bd"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir "$work/rules"

echo "-- extract additionalPrometheusRulesMap"
podman run --rm -v "$root:/repo:ro" -v "$work/rules:/rules" "$python_image" sh -ec '
  pip install --quiet --disable-pip-version-check --root-user-action=ignore \
    --only-binary :all: --require-hashes \
    -r /repo/scripts/tests/prometheus-rules/requirements.txt
  python -I /repo/scripts/extract-prometheus-rules.py \
    /repo/platform/monitoring/kube-prometheus-stack.yml /rules'
cp "$root"/scripts/tests/prometheus-rules/*.test.yml "$work/"
# The prometheus image runs as nobody, and mktemp -d is 0700.
chmod -R a+rX "$work"

promtool() {
  podman run --rm --network none -v "$work:/work:ro" -w /work \
    --entrypoint /bin/promtool "$promtool_image" "$@"
}

cd "$work"
promtool --version
echo "-- promtool check rules"
promtool check rules rules/*.yml
echo "-- promtool test rules"
promtool test rules ./*.test.yml
