#!/usr/bin/env bash
# Security checks (engineering guardrails §2) over the whole repository:
# Trivy over every configuration file it recognises (Kubernetes manifests,
# Dockerfiles, Terraform, Helm), CRITICAL and HIGH; gitleaks over every file;
# osv-scanner over every dependency lockfile it finds. The run fails on a
# finding that no reviewed list names:
#   scripts/scan-repository-baseline.toml   single Trivy and osv-scanner
#       findings that were on main when the scan started to fail the build,
#       each until a date; scripts/scan-repository-baseline.py compares the
#       two reports with it
#   .gitleaks.toml                           matches that were read and are
#       not secrets, each by rule, file and text
#
# Runs the same way locally and in CI (validate.yml, job security-scan):
#   bash scripts/scan-repository.sh
# Needs git and podman. Everything runs in podman, from images pinned by
# digest. Trivy, gitleaks, the comparison and its tests run offline, and
# gitleaks redacts what it finds. Trivy uses the checks built into its
# binary, so a new check arrives with a reviewed change of the image and not
# between two runs; it logs one ERROR line, "Falling back to embedded
# checks", because there is no downloaded bundle to prefer. osv-scanner needs
# the network, because it asks osv.dev, and so does the install of bandit.
#
# What is scanned is a copy of the files git tracks and of the new files git
# does not ignore, as they are in the working tree. Ignored files and the
# history are not scanned. A .trivyignore or an osv-scanner.toml in the
# repository has no effect: both scanners are told to read none, and what
# they report is judged by the baseline alone.
#
# Not covered: secrets in commits that a later commit removed; dependencies
# that no lockfile records (infra/irc/bots/strategos-irc-bot/pyproject.toml,
# the pip pins written inside workflows); base images and packages of the
# Dockerfiles; the providers in infra/terraform/dns/.terraform.lock.hcl; the
# Helm charts and container images that the manifests name; and the Go call
# analysis of osv-scanner, which is off so that the result does not depend on
# the Go toolchain inside its image. shellcheck and bandit run over this
# tooling only, not over the other scripts of the repository.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
shellcheck_image="docker.io/koalaman/shellcheck:v0.10.0@sha256:2097951f02e735b613f4a34de20c40f937a6c8f18ecb170612c88c34517221fb"
python_image="docker.io/library/python:3.12.16-alpine@sha256:e064671ca452259bab50e8e2c7bd896e39e9a343bb06ca25234adf6fe18d0fc7"
trivy_image="ghcr.io/aquasecurity/trivy:0.76.2@sha256:8f6bb947dbc528902e11f2f5be52f7b519f6a39d5d148f41166f2040499768fb"
gitleaks_image="ghcr.io/gitleaks/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f"
osv_scanner_image="ghcr.io/google/osv-scanner:v2.7.0@sha256:4f3b7e2c64b9d38a5febc5a7e4a3dfef1d1362f726dea7a2ef4762b46469fcd0"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir "$work/repo" "$work/out"
# A tracked file that was deleted in the working tree is not there to copy.
(cd "$root" && git ls-files -z --cached --others --exclude-standard |
  while IFS= read -r -d '' path; do
    if [ -e "$path" ] || [ -L "$path" ]; then
      printf '%s\0' "$path"
    fi
  done |
  tar --null --no-recursion --files-from=- -cf -) | tar -xf - -C "$work/repo"
# osv-scanner is given this file as its configuration, in place of any
# osv-scanner.toml beside a lockfile.
: > "$work/out/osv-scanner-no-config.toml"
# The images may run as a non-root user, and mktemp -d is 0700.
chmod -R a+rX "$work"

echo "-- shellcheck"
podman run --rm --network none -v "$work/repo:/repo:ro" -w /repo "$shellcheck_image" --version
podman run --rm --network none -v "$work/repo:/repo:ro" -w /repo "$shellcheck_image" \
  scripts/scan-repository.sh

echo "-- bandit"
podman run --rm -v "$work/repo:/repo:ro" "$python_image" sh -ec '
  pip install --quiet --disable-pip-version-check --root-user-action=ignore \
    --only-binary :all: --require-hashes \
    -r /repo/scripts/tests/prometheus-rules/requirements-security.txt
  bandit --version
  bandit /repo/scripts/scan-repository-baseline.py \
    /repo/scripts/tests/test_scan_repository_baseline.py'

echo "-- tests of the baseline comparison"
podman run --rm --network none -v "$work/repo:/repo:ro" -w /repo "$python_image" \
  python -I -B -m unittest discover -s scripts/tests -p 'test_scan_repository_baseline.py'

echo "-- gitleaks"
podman run --rm --network none -v "$work/repo:/repo:ro" -w /repo "$gitleaks_image" \
  dir . --config .gitleaks.toml --no-banner --redact --verbose

echo "-- trivy config"
podman run --rm --network none -v "$work/repo:/repo:ro" -w /repo "$trivy_image" --version
# Exit code 0 on findings: the baseline comparison below judges them. An
# error of the scan itself still ends the run here.
podman run --rm --network none -v "$work/repo:/repo:ro" -v "$work/out:/out" -w /repo \
  "$trivy_image" config --skip-check-update --ignorefile "" \
  --severity CRITICAL,HIGH --format json --output /out/trivy.json --exit-code 0 .

echo "-- osv-scanner"
# osv-scanner exits 1 when it reports a vulnerability; the baseline comparison
# below judges the report. Any other non-zero status is an error of the scan.
osv_status=0
podman run --rm -v "$work/repo:/repo:ro" -v "$work/out:/out" -w /repo "$osv_scanner_image" \
  scan source --recursive --no-call-analysis=go \
  --config /out/osv-scanner-no-config.toml \
  --format json --output-file /out/osv.json . || osv_status=$?
if [ "$osv_status" -ne 0 ] && [ "$osv_status" -ne 1 ]; then
  echo "osv-scanner ended with status $osv_status" >&2
  exit "$osv_status"
fi

echo "-- baseline"
podman run --rm --network none -v "$work/repo:/repo:ro" -v "$work/out:/out:ro" -w /repo \
  "$python_image" python -I -B scripts/scan-repository-baseline.py \
  --baseline scripts/scan-repository-baseline.toml \
  --trivy /out/trivy.json --osv /out/osv.json --osv-root /repo
