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
# The directory must not exist; the directory that holds it must. Needs
# python3 3.12 to 3.14 with pip, and network access to PyPI. The system
# site-packages are not visible inside.
#
# Without pip the run stops before it creates anything. When a later step
# fails (the venv, the install, the marker, the final import), the run removes
# the directory it created, so the next run does not stop at "already exists".
#
# The caller provides two things, and the script cannot check either:
# - while the run acquires <directory>, from its first look at the path to
#   its cd into the directory, nobody else creates, moves or removes it. An
#   empty directory put there between the mkdir and the cd would be taken
#   for the run's own;
# - while the run provisions, <directory> and the directories above it keep
#   naming the same place, and nobody else writes into the directory.
#   python3 -m venv and pip write through the path the working directory has
#   when they start, so a directory put in place of the run's own can receive
#   their files.
#
# The cleanup is narrower than that, to do no harm when a condition did not
# hold. It deletes only what is inside its working directory, removes the
# path only with rmdir, and removes nothing, and says so, when the path no
# longer names its working directory. A directory with content that took the
# place of the run's own is therefore not emptied; an empty one can be
# removed by that rmdir.
#
# Not covered: a run that is killed, a signal that ends this shell while pip
# keeps writing, an interruption between creating the directory and arming
# the cleanup, and a removal that itself fails.
set -euo pipefail

DIR="${1:-}"
if [ -z "$DIR" ]; then
  echo "usage: $0 <directory>" >&2
  exit 2
fi
# The run changes into the directory, so the path has to hold from anywhere.
case "$DIR" in
  /*) ;;
  *) DIR="$PWD/$DIR" ;;
esac
if [ -e "$DIR" ] || [ -L "$DIR" ]; then
  echo "already exists: $DIR" >&2
  exit 2
fi
if ! python3 -m pip --version >/dev/null 2>&1; then
  echo "python3 -m pip is not available on this machine; nothing was created" >&2
  exit 2
fi

REQUIREMENTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/promote-image-tag-requirements.txt"

# mkdir fails when the directory exists, also when it appeared after the check
# above. mkdir and cd are two steps: what the run enters is whatever the path
# names by then, and one that is not empty is not the run's.
mkdir -- "$DIR"
cd -- "$DIR"
if [ -n "$(find . -mindepth 1 -print -quit)" ]; then
  echo "$DIR was not empty right after this run created it; nothing was removed" >&2
  exit 1
fi

discard() {
  if ! [ "$DIR" -ef . ]; then
    echo "provisioning failed; $DIR no longer names the directory this run was working in, so nothing was removed" >&2
    return
  fi
  # What is inside the working directory, and then the path only if it is an
  # empty directory: a directory that took its place in between is not emptied.
  find . -mindepth 1 -delete && cd / && rmdir -- "$DIR" &&
    echo "provisioning failed; removed $DIR" >&2
}
trap discard EXIT

# The directory is named `.` from here on: python3 -m venv and pip take its
# path when they start, and the marker and the cleanup act on the working
# directory itself.
python3 -m venv --without-pip .
python3 -m pip --python ./bin/python install \
  --quiet --disable-pip-version-check --root-user-action=ignore \
  --only-binary :all: --require-hashes -r "$REQUIREMENTS"
# promote-image-tag.sh compares this with the requirements it finds beside it,
# so an environment provisioned from other pins is not used.
sha256sum < "$REQUIREMENTS" > promote-image-tag-requirements.sha256
./bin/python -I -B -c 'import sys, yaml; print("python", sys.version.split()[0], "PyYAML", yaml.__version__)'
trap - EXIT
