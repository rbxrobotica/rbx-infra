#!/usr/bin/env bash
#
# promote-image-tag.sh - validate and update one image tag in a prod kustomization.
#
# Usage:
#   promote-image-tag.sh <app> <image> <tag-or-image-ref>
#
# Required:
#   PROMOTE_IMAGE_TAG_ENV=<directory>  the environment provisioned by
#                     `promote-image-tag-env.sh <directory>`: a python3 with
#                     PyYAML pinned by hash, which the verifier runs in.
#
# Optional:
#   CHECK_REGISTRY=1  verify image:tag exists with `docker manifest inspect`.
#
# This script edits only apps/prod/<app>/kustomization.yml and only the image
# stanza whose `name:` exactly matches <image>.
#
# It refuses an entry that also sets `digest:`. Kustomize renders such an entry
# as image:tag@digest and the cluster pulls by digest, so a new tag beside the
# old digest keeps running the old image. Promote that image by hand, changing
# newTag and digest in the same commit.
set -euo pipefail

APP="${1:-}"
IMAGE_INPUT="${2:-}"
TAG_INPUT="${3:-}"

if [ -z "$APP" ] || [ -z "$IMAGE_INPUT" ] || [ -z "$TAG_INPUT" ]; then
  echo "usage: $0 <app> <image> <tag-or-image-ref>" >&2
  exit 2
fi

if [[ ! "$APP" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
  echo "invalid app name: $APP" >&2
  exit 2
fi

IMAGE="${IMAGE_INPUT%%@*}"
IMAGE="${IMAGE%:*}"

if [[ ! "$IMAGE" =~ ^ghcr\.io/rbxrobotica/[a-z0-9._-]+$ ]]; then
  echo "invalid image: $IMAGE_INPUT (expected ghcr.io/rbxrobotica/<name>)" >&2
  exit 2
fi

case "$TAG_INPUT" in
  "$IMAGE":*) TAG="${TAG_INPUT##*:}" ;;
  *) TAG="$TAG_INPUT" ;;
esac

if [[ ! "$TAG" =~ ^sha-[0-9a-f]{7,64}$ ]]; then
  echo "invalid tag: $TAG (expected sha-<hex>)" >&2
  exit 2
fi

KUSTOMIZATION="apps/prod/${APP}/kustomization.yml"
if [ ! -f "$KUSTOMIZATION" ]; then
  echo "missing kustomization: $KUSTOMIZATION" >&2
  exit 2
fi

if ! grep -Fq "name: ${IMAGE}" "$KUSTOMIZATION"; then
  echo "image ${IMAGE} is not declared in ${KUSTOMIZATION}" >&2
  exit 2
fi

IMAGE_STANZA_COUNT="$(grep -Ec '^[[:space:]]*-[[:space:]]*name:[[:space:]]*' "$KUSTOMIZATION" || true)"
if [ "$IMAGE_STANZA_COUNT" -gt 1 ] && [ "${ALLOW_PARTIAL_MULTI_IMAGE:-0}" != "1" ]; then
  echo "${KUSTOMIZATION} declares ${IMAGE_STANZA_COUNT} images; refusing partial promotion without ALLOW_PARTIAL_MULTI_IMAGE=1" >&2
  exit 2
fi

if [ "${CHECK_REGISTRY:-0}" = "1" ]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "docker is required for CHECK_REGISTRY=1" >&2
    exit 2
  fi
  if ! docker manifest inspect "${IMAGE}:${TAG}" >/dev/null 2>&1; then
    echo "image tag does not exist or is not readable: ${IMAGE}:${TAG}" >&2
    exit 1
  fi
fi

# The verifier runs in the environment of promote-image-tag-env.sh and in no
# other: whatever PyYAML this machine happens to have is not used.
SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERIFY_PYTHON="${PROMOTE_IMAGE_TAG_ENV:-}/bin/python"
PINS="$(sha256sum < "${SCRIPTS}/promote-image-tag-requirements.txt")"
if [ -z "${PROMOTE_IMAGE_TAG_ENV:-}" ] || [ ! -x "$VERIFY_PYTHON" ] ||
  [ "$(cat "${PROMOTE_IMAGE_TAG_ENV}/promote-image-tag-requirements.sha256" 2>/dev/null)" != "$PINS" ]; then
  echo "PROMOTE_IMAGE_TAG_ENV is not an environment of the current scripts/promote-image-tag-requirements.txt" >&2
  echo "provision one with: bash scripts/promote-image-tag-env.sh <directory>" >&2
  exit 2
fi

TMP="$(mktemp)"
awk -v image="$IMAGE" -v tag="$TAG" '
  function trim(s) {
    sub(/^[[:space:]]+/, "", s)
    sub(/[[:space:]]+$/, "", s)
    return s
  }
  /^[[:space:]]*-[[:space:]]*name:[[:space:]]*/ {
    name = $0
    sub(/^[[:space:]]*-[[:space:]]*name:[[:space:]]*/, "", name)
    name = trim(name)
    in_target = (name == image)
    print
    next
  }
  in_target && /^[[:space:]]*newTag:[[:space:]]*/ {
    indent = $0
    sub(/newTag:.*/, "", indent)
    print indent "newTag: " tag
    updated += 1
    in_target = 0
    next
  }
  { print }
  END {
    if (updated != 1) {
      exit 42
    }
  }
' "$KUSTOMIZATION" > "$TMP" || {
  rc=$?
  rm -f "$TMP"
  if [ "$rc" = 42 ]; then
    echo "could not update exactly one newTag for ${IMAGE} in ${KUSTOMIZATION}" >&2
  fi
  exit "$rc"
}

# The edit above works by line, and YAML has more ways of giving an entry a
# digest than a line reader can tell. The verifier parses the kustomization and
# the edited copy, and the copy is installed only if it agrees: the entry has
# no digest and its newTag is all that changed.
if ! "$VERIFY_PYTHON" -I -B "${SCRIPTS}/promote-image-tag-verify.py" "$KUSTOMIZATION" "$TMP" "$IMAGE" "$TAG"; then
  rm -f "$TMP"
  exit 2
fi

mv "$TMP" "$KUSTOMIZATION"
echo "Updated ${KUSTOMIZATION}: ${IMAGE}:${TAG}"
