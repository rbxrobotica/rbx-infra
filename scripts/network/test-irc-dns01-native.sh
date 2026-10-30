#!/usr/bin/env bash
# Build uses authenticated distro package repositories; runtime has no network.
# Never mount live configuration, credentials, home directories or host sockets.
set -euo pipefail
umask 077
(( $# == 0 )) || { echo 'No arguments accepted.' >&2; exit 2; }
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
engine=(env -u CONTAINER_HOST -u CONTAINER_CONNECTION podman --remote=false)
[[ $(id -u) != 0 ]] || { echo 'Non-root caller required.' >&2; exit 2; }
[[ $("${engine[@]}" info --format '{{.Host.Security.Rootless}}') == true ]]
scratch=$(mktemp -d /tmp/rbx-irc-dns01.XXXXXXXX)
name="rbx-irc-dns01-${scratch##*.}"
cleanup() {
    "${engine[@]}" rm --force --ignore "$name" >/dev/null 2>&1 || true
    rm -rf -- "$scratch"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
cp "$repo/scripts/tests/fixtures/irc-dns01/Containerfile" "$scratch/"
cp "$repo/scripts/tests/fixtures/irc-dns01/runner.py" "$scratch/"
cp "$repo/infra/irc/acme/dns01-plan.json" "$scratch/plan.json"
if ! timeout --kill-after=10s 600s "${engine[@]}" build --iidfile "$scratch/image-id" \
    -f "$scratch/Containerfile" "$scratch" >"$scratch/build.log" 2>&1; then
    echo 'Isolated fixture build failed; no production access performed.' >&2
    exit 1
fi
image=$(cat "$scratch/image-id")
if ! timeout --kill-after=5s 150s "${engine[@]}" run --rm --name "$name" \
    --network none --read-only --read-only-tmpfs=false --cap-drop ALL \
    --security-opt no-new-privileges --user 10001:10001 \
    --pid private --ipc private --uts private --cgroupns private \
    --pids-limit 128 --memory 768m --memory-swap 768m --cpus 2 \
    --timeout 120 --log-driver none \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=384m,mode=1777 \
    --env RBX_DNS01_SYNTHETIC=1 "$image" >"$scratch/result.json" 2>"$scratch/error.log"; then
    echo 'Native RFC2136 isolation failed; transient logs discarded.' >&2
    exit 1
fi
python3 -I -B - "$scratch/result.json" "$scratch/plan.json" "$image" <<'PY'
import hashlib, json, pathlib, sys
report = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert report['status'] == 'passed'
assert len(report['cases']) == 10 and all(c['passed'] for c in report['cases'])
assert report['candidate_sha256'] == hashlib.sha256(pathlib.Path(sys.argv[2]).read_bytes()).hexdigest()
report['fixture_image_id'] = sys.argv[3]
report['state_storage'] = 'disposable_tmpfs'
print(json.dumps(report, sort_keys=True))
PY
