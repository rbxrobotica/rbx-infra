#!/usr/bin/env bash
# Fail a change that gives a digest-pinned image a new tag beside its old
# digest: an images entry of a kustomization under apps/ whose newTag changed
# while its digest stayed as it was. Kustomize renders such an entry as
# image:tag@digest and the cluster pulls by digest, so the old image would keep
# running under the new tag.
#
# Usage, from inside the repository to check:
#   check-digest-pin-promotion.sh <base>...<head>
#       what <head> changed since it left <base>, as a pull request shows it
#   check-digest-pin-promotion.sh <base>..<head>
#       <head> against <base> itself, as a push moves a branch
#
# validate.yml (job image-digest-pins) runs it with origin/<base branch>...HEAD
# on a pull request, where HEAD is the merge commit GitHub makes for the pull
# request: the comparison is between the tip of the base branch in that merge
# and its result. On a push to main it runs with <commit before the push>..HEAD
# and reports after the fact: ArgoCD syncs main by itself. Before opening a
# pull request:
#   bash scripts/check-digest-pin-promotion.sh origin/main...HEAD
#
# Git runs here, where the repository is. The kustomization files of the two
# commits are then read in podman, from an image pinned by digest, in the
# environment scripts/promote-image-tag-env.sh provisions (PyYAML pinned by
# hash), by scripts/check-digest-pin-promotion.py. That file holds the rules
# and says what this check does not see.
#
# Exit status: 0 nothing to refuse, 1 a finding, anything else the two commits
# could not be compared.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_image="docker.io/library/python:3.12.16-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df"

usage() {
  echo "usage: $0 <base>...<head> | <base>..<head>" >&2
  exit 2
}

commit() {
  case "$1" in
    "" | -*) usage ;;
  esac
  git rev-parse --verify --quiet "$1^{commit}" || {
    echo "not a commit of this repository: $1" >&2
    exit 2
  }
}

[ "$#" -eq 1 ] || usage
# The paths below are paths of the repository, whatever directory this runs in.
top="$(git rev-parse --show-toplevel)" || {
  echo "run this inside the work tree of the repository to check" >&2
  exit 2
}
cd "$top"
case "$1" in
  *...*)
    left="$(commit "${1%%...*}")" || exit 2
    head="$(commit "${1#*...}")" || exit 2
    if [ "$(git rev-parse --is-shallow-repository)" != false ]; then
      echo "this clone is shallow: the commit where ${1#*...} left ${1%%...*} may be missing from it" >&2
      exit 2
    fi
    base="$(git merge-base "$left" "$head")" || {
      echo "${1%%...*} and ${1#*...} have no common ancestor" >&2
      exit 2
    }
    ;;
  *..*)
    base="$(commit "${1%%..*}")" || exit 2
    head="$(commit "${1#*..}")" || exit 2
    ;;
  *) usage ;;
esac

echo "comparing ${base} (base) with ${head} (head)"
# What the reader gets: the two listings and the contents of every
# kustomization file in them, named by object id. It never sees a path of the
# repository as a path of its own.
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir "$work/blobs"
for side in base head; do
  git ls-tree -r -z "${!side}" -- apps > "$work/$side.tree"
done
# A symbolic link or a submodule under apps/ may lead out of it, to something
# that changed while nothing under apps/ did. The reader refuses those.
if git diff-tree --quiet "$base" "$head" -- apps && ! grep -qzE '^1[26]0000 ' "$work/head.tree"; then
  echo "nothing under apps/ differs between the two commits"
  exit 0
fi

for side in base head; do
  while IFS= read -r -d '' record; do
    path="${record#*$'\t'}"
    case "${path##*/}" in
      kustomization.yaml | kustomization.yml | Kustomization) ;;
      *) continue ;;
    esac
    read -r _ kind object <<< "${record%%$'\t'*}"
    if [ "$kind" = blob ] && [ ! -e "$work/blobs/$object" ]; then
      git cat-file blob "$object" > "$work/blobs/$object"
    fi
  done < "$work/$side.tree"
done

podman run --rm -v "$root:/repo:ro" -v "$work:/export:ro" "$python_image" sh -ec '
  bash /repo/scripts/promote-image-tag-env.sh /tmp/promote-image-tag-env || exit 2
  exec /tmp/promote-image-tag-env/bin/python -I -B /repo/scripts/check-digest-pin-promotion.py /export'
