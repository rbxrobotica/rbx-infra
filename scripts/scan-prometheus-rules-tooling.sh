#!/usr/bin/env bash
# Security checks (engineering guardrails §2) for the Prometheus rule tooling
# that scripts/test-prometheus-rules.sh runs: bandit over the extractor,
# osv-scanner over its hash-pinned Python requirements, and gitleaks over
# every file of the tooling, its tests and the workflow that runs them.
#
# Runs the same way locally and in CI (validate.yml, job prometheus-rules):
#   bash scripts/scan-prometheus-rules-tooling.sh
# Everything runs in podman, from images pinned by digest. bandit and its
# dependencies are pinned by hash in
# scripts/tests/prometheus-rules/requirements-security.txt. gitleaks runs
# offline and redacts what it finds.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_image="docker.io/library/python:3.12.16-alpine@sha256:e064671ca452259bab50e8e2c7bd896e39e9a343bb06ca25234adf6fe18d0fc7"
osv_scanner_image="ghcr.io/google/osv-scanner:v2.7.0@sha256:4f3b7e2c64b9d38a5febc5a7e4a3dfef1d1362f726dea7a2ef4762b46469fcd0"
gitleaks_image="ghcr.io/gitleaks/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f"
tooling=(
  .github/workflows/validate.yml
  scripts/extract-prometheus-rules.py
  scripts/scan-prometheus-rules-tooling.sh
  scripts/test-prometheus-rules.sh
  scripts/tests/prometheus-rules
)

echo "-- bandit"
podman run --rm -v "$root:/repo:ro" "$python_image" sh -ec '
  pip install --quiet --disable-pip-version-check --root-user-action=ignore \
    --only-binary :all: --require-hashes \
    -r /repo/scripts/tests/prometheus-rules/requirements-security.txt
  bandit --version
  bandit /repo/scripts/extract-prometheus-rules.py'

echo "-- osv-scanner"
podman run --rm -v "$root:/repo:ro" -w /repo "$osv_scanner_image" scan source \
  --lockfile scripts/tests/prometheus-rules/requirements.txt \
  --lockfile scripts/tests/prometheus-rules/requirements-security.txt

echo "-- gitleaks"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
(cd "$root" && cp -r --parents "${tooling[@]}" "$work/")
# The gitleaks image may run as a non-root user, and mktemp -d is 0700.
chmod -R a+rX "$work"
podman run --rm --network none -v "$work:/scan:ro" "$gitleaks_image" \
  dir /scan --no-banner --redact --verbose
