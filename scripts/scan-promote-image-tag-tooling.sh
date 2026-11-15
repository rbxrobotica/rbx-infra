#!/usr/bin/env bash
# Security checks (engineering guardrails §2) for the image promotion tooling:
# its shell scripts go through shellcheck, the verifier and its tests through
# bandit, the hash-pinned Python requirements they run with through
# osv-scanner, and every file of the tooling and the workflows that run it
# through gitleaks.
#
# Runs the same way locally and in CI (validate.yml, job
# image-promotion-tooling):
#   bash scripts/scan-promote-image-tag-tooling.sh
# Everything runs in podman, from images pinned by digest. bandit and its
# dependencies are pinned by hash in
# scripts/tests/prometheus-rules/requirements-security.txt. The shell lint and
# gitleaks run offline, and gitleaks redacts what it finds.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
shellcheck_image="docker.io/koalaman/shellcheck:v0.10.0@sha256:2097951f02e735b613f4a34de20c40f937a6c8f18ecb170612c88c34517221fb"
python_image="docker.io/library/python:3.12.16-alpine@sha256:e064671ca452259bab50e8e2c7bd896e39e9a343bb06ca25234adf6fe18d0fc7"
osv_scanner_image="ghcr.io/google/osv-scanner:v2.7.0@sha256:4f3b7e2c64b9d38a5febc5a7e4a3dfef1d1362f726dea7a2ef4762b46469fcd0"
gitleaks_image="ghcr.io/gitleaks/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f"
tooling=(
  .github/workflows/image-update.yml
  .github/workflows/validate.yml
  scripts/promote-image-tag.sh
  scripts/promote-image-tag-env.sh
  scripts/promote-image-tag-requirements.txt
  scripts/promote-image-tag-verify.py
  scripts/scan-promote-image-tag-tooling.sh
  scripts/test-promote-image-tag.sh
  scripts/tests/test_promote_image_tag.py
)

echo "-- shellcheck"
podman run --rm --network none -v "$root:/repo:ro" -w /repo "$shellcheck_image" --version
podman run --rm --network none -v "$root:/repo:ro" -w /repo "$shellcheck_image" \
  scripts/promote-image-tag.sh \
  scripts/promote-image-tag-env.sh \
  scripts/scan-promote-image-tag-tooling.sh \
  scripts/test-promote-image-tag.sh

echo "-- bandit"
podman run --rm -v "$root:/repo:ro" "$python_image" sh -ec '
  pip install --quiet --disable-pip-version-check --root-user-action=ignore \
    --only-binary :all: --require-hashes \
    -r /repo/scripts/tests/prometheus-rules/requirements-security.txt
  bandit --version
  bandit /repo/scripts/promote-image-tag-verify.py /repo/scripts/tests/test_promote_image_tag.py'

echo "-- osv-scanner"
podman run --rm -v "$root:/repo:ro" -w /repo "$osv_scanner_image" scan source \
  --lockfile requirements.txt:scripts/promote-image-tag-requirements.txt \
  --lockfile scripts/tests/prometheus-rules/requirements-security.txt

echo "-- gitleaks"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
(cd "$root" && cp -r --parents "${tooling[@]}" "$work/")
# The gitleaks image may run as a non-root user, and mktemp -d is 0700.
chmod -R a+rX "$work"
podman run --rm --network none -v "$work:/scan:ro" "$gitleaks_image" \
  dir /scan --no-banner --redact --verbose
