#!/usr/bin/env bash
#
# audit-local-path-pv-dirs.sh - read-only local-path PV directory audit.
# Prints whether each local-path PV's node-local directory exists on its owner node.
set -euo pipefail

namespace_filter="${1:-}"
read -r -a kubectl_cmd <<< "${KUBECTL:-kubectl}"
read -r -a ssh_cmd <<< "${SSH_CMD:-ssh}"

if ! command -v "${kubectl_cmd[0]}" >/dev/null 2>&1; then
  echo "${kubectl_cmd[0]} not found" >&2
  exit 127
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "jq not found" >&2
  exit 127
fi

quote() {
  printf '%q' "$1"
}

"${kubectl_cmd[@]}" get pv -o json |
  jq -r --arg ns "$namespace_filter" '
    .items[]
    | select(.spec.storageClassName == "local-path")
    | select(($ns == "") or (.spec.claimRef.namespace == $ns))
    | [
        .metadata.name,
        (.spec.claimRef.namespace // "-"),
        (.spec.claimRef.name // "-"),
        (.spec.capacity.storage // "-"),
        (
          [
            .spec.nodeAffinity.required.nodeSelectorTerms[]?.matchExpressions[]?
            | select(.key == "kubernetes.io/hostname")
            | .values[]?
          ][0] // "-"
        ),
        (.spec.local.path // .spec.hostPath.path // "-")
      ]
    | @tsv
  ' |
  while IFS=$'\t' read -r pv namespace claim size node path; do
    if [[ "$node" == "-" || "$path" == "-" ]]; then
      printf 'UNKNOWN\t%s\t%s/%s\t%s\t%s\t%s\n' "$pv" "$namespace" "$claim" "$size" "$node" "$path"
      continue
    fi

    remote_path="$(quote "$path")"
    if "${ssh_cmd[@]}" -o BatchMode=yes -o ConnectTimeout=8 "$node" "test -d $remote_path" >/dev/null 2>&1 </dev/null; then
      stat_line="$("${ssh_cmd[@]}" -o BatchMode=yes -o ConnectTimeout=8 "$node" "stat -c '%a %u:%g %n' $remote_path" </dev/null)"
      printf 'OK\t%s\t%s/%s\t%s\t%s\t%s\n' "$pv" "$namespace" "$claim" "$size" "$node" "$stat_line"
    else
      printf 'MISSING\t%s\t%s/%s\t%s\t%s\t%s\n' "$pv" "$namespace" "$claim" "$size" "$node" "$path"
    fi
  done
