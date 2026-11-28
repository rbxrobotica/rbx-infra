#!/usr/bin/env bash
# Security checks (engineering guardrails §2) over the whole repository:
# Trivy over every configuration file it recognises (Kubernetes manifests,
# Dockerfiles, Terraform, Helm), gitleaks over every file, and osv-scanner
# over every dependency lockfile it finds. Each of the three fails the run on
# a finding that its reviewed ignore file does not name:
#   .trivyignore.yaml   findings that were on main when the scan started to
#                       fail the build, by check and file, each with a date
#   .gitleaks.toml      matches that were read and are not secrets
#   osv-scanner.toml    known vulnerabilities, each with a date
#
# Runs the same way locally and in CI (validate.yml, job security-scan):
#   bash scripts/scan-repository.sh
# Needs git and podman. Everything runs in podman, from images pinned by
# digest. Trivy and gitleaks run offline, and gitleaks redacts what it finds.
# Trivy uses the checks built into its binary, so a new check arrives with a
# reviewed change of the image and not between two runs; it logs one ERROR
# line, "Falling back to embedded checks", because there is no downloaded
# bundle to prefer. osv-scanner needs the network: it asks osv.dev.
#
# What is scanned is a copy of the files git tracks and of the new files git
# does not ignore, as they are in the working tree. Ignored files and the
# history are not scanned.
#
# Not covered: dependencies that no lockfile records
# (infra/irc/bots/strategos-irc-bot/pyproject.toml, the pip pins written
# inside workflows), base images and packages of the Dockerfiles, the
# providers in infra/terraform/dns/.terraform.lock.hcl, the Helm charts and
# container images that the manifests name, and the Go call analysis of
# osv-scanner, which is off so that the result does not depend on the Go
# toolchain inside its image.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
shellcheck_image="docker.io/koalaman/shellcheck:v0.10.0@sha256:2097951f02e735b613f4a34de20c40f937a6c8f18ecb170612c88c34517221fb"
trivy_image="ghcr.io/aquasecurity/trivy:0.76.2@sha256:8f6bb947dbc528902e11f2f5be52f7b519f6a39d5d148f41166f2040499768fb"
gitleaks_image="ghcr.io/gitleaks/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f"
osv_scanner_image="ghcr.io/google/osv-scanner:v2.7.0@sha256:4f3b7e2c64b9d38a5febc5a7e4a3dfef1d1362f726dea7a2ef4762b46469fcd0"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir "$work/repo"
# A tracked file that was deleted in the working tree is not there to copy.
(cd "$root" && git ls-files -z --cached --others --exclude-standard |
  while IFS= read -r -d '' path; do
    if [ -e "$path" ] || [ -L "$path" ]; then
      printf '%s\0' "$path"
    fi
  done |
  tar --null --no-recursion --files-from=- -cf -) | tar -xf - -C "$work/repo"
# The images may run as a non-root user, and mktemp -d is 0700.
chmod -R a+rX "$work"

echo "-- shellcheck"
podman run --rm --network none -v "$work/repo:/repo:ro" -w /repo "$shellcheck_image" --version
podman run --rm --network none -v "$work/repo:/repo:ro" -w /repo "$shellcheck_image" \
  scripts/scan-repository.sh

echo "-- trivy config"
podman run --rm --network none -v "$work/repo:/repo:ro" -w /repo "$trivy_image" --version
podman run --rm --network none -v "$work/repo:/repo:ro" -w /repo "$trivy_image" config \
  --skip-check-update --ignorefile .trivyignore.yaml \
  --severity CRITICAL,HIGH --table-mode detailed --exit-code 1 .

echo "-- gitleaks"
podman run --rm --network none -v "$work/repo:/repo:ro" -w /repo "$gitleaks_image" \
  dir . --config .gitleaks.toml --no-banner --redact --verbose

echo "-- osv-scanner"
podman run --rm -v "$work/repo:/repo:ro" -w /repo "$osv_scanner_image" scan source \
  --recursive --no-call-analysis=go --config osv-scanner.toml .
