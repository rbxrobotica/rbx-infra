#!/usr/bin/env bash
#
# promote-image-tag.sh - validate and update one image tag in a prod kustomization.
#
# Usage:
#   promote-image-tag.sh <app> <image> <tag-or-image-ref>
#
# Optional:
#   CHECK_REGISTRY=1  verify image:tag exists with `docker manifest inspect`.
#
# This script edits only apps/prod/<app>/kustomization.yml and only the image
# stanza whose `name:` exactly matches <image>.
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

mv "$TMP" "$KUSTOMIZATION"
echo "Updated ${KUSTOMIZATION}: ${IMAGE}:${TAG}"
