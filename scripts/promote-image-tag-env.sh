#!/usr/bin/env bash
# Provision the environment scripts/promote-image-tag.sh runs its verifier in:
# a virtual environment of this machine's python3 that holds PyYAML and
# nothing else, pinned by hash in scripts/promote-image-tag-requirements.txt.
#
# Usage:
#   promote-image-tag-env.sh <directory>
#
# Then run the promotion with PROMOTE_IMAGE_TAG_ENV=<directory>.
# .github/workflows/image-update.yml and the tests provision it the same way.
# The directory must not exist. Needs python3 3.12 to 3.14 with pip, and
# network access to PyPI. The system site-packages are not visible inside.
set -euo pipefail

DIR="${1:-}"
if [ -z "$DIR" ]; then
  echo "usage: $0 <directory>" >&2
  exit 2
fi
if [ -e "$DIR" ]; then
  echo "already exists: $DIR" >&2
  exit 2
fi

REQUIREMENTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/promote-image-tag-requirements.txt"

python3 -m venv --without-pip "$DIR"
python3 -m pip --python "$DIR/bin/python" install \
  --quiet --disable-pip-version-check --root-user-action=ignore \
  --only-binary :all: --require-hashes -r "$REQUIREMENTS"
# promote-image-tag.sh compares this with the requirements it finds beside it,
# so an environment provisioned from other pins is not used.
sha256sum < "$REQUIREMENTS" > "$DIR/promote-image-tag-requirements.sha256"
"$DIR/bin/python" -I -B -c 'import sys, yaml; print("python", sys.version.split()[0], "PyYAML", yaml.__version__)'
