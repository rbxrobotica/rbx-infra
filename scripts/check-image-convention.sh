#!/usr/bin/env bash
#
# check-image-convention.sh — enforce Pattern R image conventions.
#
# Usage: check-image-convention.sh <manifest-file> [<manifest-file>...]
#
# Rules (applied to `image:` references in the given apps/ manifest files):
#
#   1. ORG REGISTRY: an RBX application image MUST live under
#      ghcr.io/rbxrobotica/. Non-org RBX assets (ghcr.io/ldamasio/*,
#      docker.io/ldamasio/*, etc.) are violations. Well-known third-party
#      images (postgres, redis, busybox, litellm, migrate, ...) are exempt
#      via ALLOW_THIRDPARTY below.
#
#   2. NO HARDCODED SHA IN DEPLOYMENTS: in a Deployment manifest (file named
#      *deploy*), an org image MUST NOT hardcode an immutable `:sha-<hex>` tag.
#      The tag belongs in the kustomize `images:`-transformer (kustomization.yml
#      `newTag`); the deploy manifest uses the bare name or `:latest`.
#
# Exit 1 on any violation. Designed to run on the manifest files CHANGED by a
# PR (changed-files scoped), so it blocks new non-conformance without failing
# on the pre-existing backlog.
#
# To allow a new third-party image, add its repo (tag-stripped) to the regex.
set -euo pipefail

# Third-party images exempt from Rule 1 (matched against the tag-stripped ref).
ALLOW_THIRDPARTY='^(postgres|redis|busybox|rabbitmq|python|alpine|nginx|paradedb/paradedb|migrate/migrate|docker\.io/migrate/migrate|ghcr\.io/berriai/litellm|squidfunk/mkdocs|minio/minio)([:@]|$)'

violations=0

for f in "$@"; do
  [ -f "$f" ] || continue
  case "$f" in *.yml|*.yaml) ;; *) continue ;; esac

  is_deploy=0
  case "$(basename "$f")" in *deploy*) is_deploy=1 ;; esac

  # Extract every `image: <ref>` value in the file.
  while IFS= read -r img; do
    [ -z "$img" ] && continue
    [ "$img" = "__IMAGE__" ] && continue            # templating placeholder
    img="${img#\"}"; img="${img%\"}"
    img="${img#\'}"; img="${img%\'}"

    if [[ "$img" != ghcr.io/rbxrobotica/* ]]; then
      # Rule 1: must be org registry, unless third-party.
      if [[ ! "$img" =~ $ALLOW_THIRDPARTY ]]; then
        echo "::error file=$f::Rule 1 (org registry): $img — RBX app images must be ghcr.io/rbxrobotica/*"
        violations=$((violations + 1))
        continue
      fi
    else
      # Rule 2: org image in a Deployment must not hardcode :sha-<hex>.
      if [ "$is_deploy" = 1 ] && [[ "$img" =~ :sha-[0-9a-f]+(@|$) ]]; then
        echo "::error file=$f::Rule 2 (no hardcoded sha): $img — use the kustomize images-transformer (kustomization.yml newTag), not a sha in the Deployment"
        violations=$((violations + 1))
      fi
    fi
  done < <(grep -hoE 'image:[[:space:]]*[^[:space:]]+' "$f" | sed -E 's/image:[[:space:]]*//')
done

if [ "$violations" -gt 0 ]; then
  echo "::error::$violations Pattern R image-convention violation(s) above. See scripts/check-image-convention.sh."
  exit 1
fi
echo "OK: no Pattern R image-convention violations in the changed manifests."
