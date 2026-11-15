#!/usr/bin/env bash
# Unit-test the image promotion tooling: scripts/promote-image-tag.sh and
# scripts/promote-image-tag-verify.py with
# scripts/tests/test_promote_image_tag.py, and the check that a change does not
# leave an old digest beside a new tag, scripts/check-digest-pin-promotion.py
# and .sh, with scripts/tests/test_check_digest_pin_promotion.py (which draws
# random lists and asks scripts/tests/kustomize_images_model.py what kustomize
# renders for them) and test_check_digest_pin_promotion_git.py.
#
# Runs the same way locally and in CI (validate.yml, job
# image-promotion-tooling):
#   bash scripts/test-promote-image-tag.sh
# The first two test files run in podman, from an image pinned by digest:
# Debian's bash, mawk and grep, and python 3.12.
# scripts/promote-image-tag-env.sh provisions the environment there as it does
# for a promotion, with PyYAML pinned by hash in
# scripts/promote-image-tag-requirements.txt, and the tests run in it. The
# repository is mounted read-only; the tests write only to the container's
# temporary directory.
#
# The third runs on this machine, with its python3 and git: it calls
# scripts/check-digest-pin-promotion.sh on throwaway repositories, and that
# script starts the same image itself.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_image="docker.io/library/python:3.12.16-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df"

podman run --rm -v "$root:/repo:ro" "$python_image" sh -ec '
  bash /repo/scripts/promote-image-tag-env.sh /tmp/promote-image-tag-env
  for tests in test_promote_image_tag.py test_check_digest_pin_promotion.py; do
    /tmp/promote-image-tag-env/bin/python -I -B -m unittest discover \
      -s /repo/scripts/tests -p "$tests" -v
  done'

python3 -I -B -m unittest discover \
  -s "$root/scripts/tests" -p "test_check_digest_pin_promotion_git.py" -v
