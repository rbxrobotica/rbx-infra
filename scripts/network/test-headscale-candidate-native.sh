#!/usr/bin/env bash
# Reproducible native checks of synthetic fixtures only. Never run Headscale on
# the host, accept live files, mount real state, or expose a network listener.
set -euo pipefail
umask 077
if (( $# > 1 )); then
    printf '%s\n' 'Usage: test-headscale-candidate-native.sh [verified-release.deb]' >&2
    exit 2
fi
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
for tool in podman python3 dpkg-deb sha256sum timeout; do
    command -v "$tool" >/dev/null || { printf 'Missing prerequisite: %s\n' "$tool" >&2; exit 2; }
done
# Remote environment selection happens early in some Podman releases. Clear it
# as well as forcing the local engine for info, execution and cleanup.
podman_local=(env -u CONTAINER_HOST -u CONTAINER_CONNECTION podman --remote=false)
if [[ $(id -u) == 0 || $("${podman_local[@]}" info --format '{{.Host.Security.Rootless}}') != true ]]; then
    printf '%s\n' 'Rootless Podman and a non-root caller are required.' >&2
    exit 2
fi
python3 -I -B -c 'import jinja2, yaml' >/dev/null
[[ $(uname -m) == x86_64 ]] || { printf '%s\n' 'This pinned fixture supports linux/amd64 only.' >&2; exit 2; }
image='docker.io/library/ubuntu@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55'
deb_sha='14eccf8d41fd93927ad3fe1fd9e49dbe14d5668a244c4205d940967c442aea0d'
release='https://github.com/juanfont/headscale/releases/download/v0.29.3/headscale_0.29.3_linux_amd64.deb'
scratch=$(mktemp -d /tmp/rbx-headscale-native.XXXXXXXX)
container="rbx-headscale-native-${scratch##*.}"
cleanup() {
    "${podman_local[@]}" rm --force --ignore "$container" >/dev/null 2>&1 || true
    rm -rf -- "$scratch"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if (( $# == 1 )); then
    cp -- "$1" "$scratch/headscale.deb"
else
    command -v curl >/dev/null
    curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
        --retry 2 --max-time 120 "$release" --output "$scratch/headscale.deb"
fi
printf '%s  %s\n' "$deb_sha" "$scratch/headscale.deb" | sha256sum --check --strict >/dev/null
dpkg-deb --extract "$scratch/headscale.deb" "$scratch/package"
python3 -I -B "$repo/scripts/tests/fixtures/headscale/prepare-native-fixtures.py" "$repo" "$scratch" \
    >"$scratch/preparation.log" 2>&1 || {
        printf '%s\n' 'Synthetic preparation/rejection checks failed before native execution.' >&2
        exit 1
    }
# keep-id makes private synthetic files readable by the unprivileged caller's
# mapped UID without relaxing their modes. Only the three mounts below exist;
# the home directory, host database, keys, sockets and live config are not used.
if ! timeout --kill-after=5s 150s "${podman_local[@]}" run --rm --name "$container" \
    --network none --cap-drop ALL --security-opt no-new-privileges \
    --read-only --read-only-tmpfs=false --userns keep-id --user "$(id -u):$(id -g)" \
    --pid private --ipc private --uts private --cgroupns private \
    --pids-limit 128 --memory 512m --memory-swap 512m --cpus 2 \
    --ulimit nofile=1024:1024 --timeout 120 --log-driver none \
    --tmpfs /var/lib/headscale:rw,nosuid,nodev,noexec,size=64m,mode=1777 \
    --tmpfs /run:rw,nosuid,nodev,noexec,size=16m,mode=1777 \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777 --workdir /tmp \
    --mount "type=bind,source=$scratch/package/usr/bin/headscale,destination=/usr/local/bin/headscale,ro" \
    --mount "type=bind,source=$scratch/bundle,destination=/etc/headscale,ro" \
    --mount "type=bind,source=$scratch/fixtures,destination=/fixtures,ro" \
    "$image" /usr/bin/env -i PATH=/usr/bin:/bin HOME=/tmp/home \
        RBX_HEADSCALE_SYNTHETIC_SANDBOX=1 /bin/sh /fixtures/native-runner.sh \
        >"$scratch/native.log" 2>&1; then
    printf '%s\n' 'Isolated native validation failed; no live state was read or changed.' >&2
    exit 1
fi
python3 -I -B - "$scratch" "$image" "$deb_sha" <<'PY'
import hashlib
import json
from pathlib import Path
import sys
scratch = Path(sys.argv[1])
log = (scratch / "native.log").read_text()
assert "RBX_NATIVE_CONFIG_AND_MATRIX_PASS" in log
assert "RBX_NATIVE_NEGATIVES_PASS" in log
report = json.loads((scratch / "test-report.json").read_text())
for name, expected in (("config.yaml", "executed_config_sha256"), ("policy.hujson", "executed_policy_sha256")):
    assert hashlib.sha256((scratch / "bundle" / name).read_bytes()).hexdigest() == report[expected]
report.update(status="passed", native_configtest=True, native_policy_matrix=True,
              native_rejections=["invalid-schema", "missing-identity", "reversed-ports"],
              image=sys.argv[2], release_deb_sha256=sys.argv[3], network="none",
              binary_sha256=hashlib.sha256((scratch / "package/usr/bin/headscale").read_bytes()).hexdigest(),
              state_storage="disposable_tmpfs", raw_logs_retained=False)
print(json.dumps(report, sort_keys=True))
PY
