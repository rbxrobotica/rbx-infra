#!/usr/bin/env bash
#
# report-p1-image-debt.sh - static report for P1 image-promotion hardening.
# Read-only: prints current backlog; does not fail on findings.
set -euo pipefail

section() {
  printf '\n## %s\n' "$1"
}

section "Production kustomizations with newTag: latest"
grep -RInE '^[[:space:]]*newTag:[[:space:]]*latest[[:space:]]*$' \
  apps/prod --include='kustomization.yml' --include='kustomization.yaml' || true

section "Production manifests with image: *:latest placeholders"
grep -RInE '^[[:space:]]*image:[[:space:]]*[^[:space:]]+:latest[[:space:]]*$' \
  apps/prod --include='*.yml' --include='*.yaml' || true

section "ArgoCD Image Updater apps using newest-build"
grep -RInE 'argocd-image-updater\.argoproj\.io/.*\.update-strategy:[[:space:]]*newest-build' \
  gitops/app-of-apps --include='*.yml' --include='*.yaml' || true

section "Image Updater direct main write-back"
grep -RInE 'argocd-image-updater\.argoproj\.io/(write-back-method:[[:space:]]*git|git-branch:[[:space:]]*main)' \
  gitops/app-of-apps --include='*.yml' --include='*.yaml' || true

section "ArgoCD Applications tracking targetRevision: main"
grep -RInE '^[[:space:]]*targetRevision:[[:space:]]*main[[:space:]]*$' \
  gitops/app-of-apps --include='*.yml' --include='*.yaml' || true
