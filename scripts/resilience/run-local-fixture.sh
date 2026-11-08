#!/usr/bin/env bash
# Synthetic-only execution with no network, credentials or host-state mounts.
set -euo pipefail
umask 077
if (( $# != 0 )); then
    printf '%s\n' 'This synthetic fixture accepts no arguments.' >&2
    exit 2
fi
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
image='ghcr.io/rbxrobotica/comms-backup@sha256:fc4a6037f035baa0d229bd86f744ceca1ddb720e672004d0b7ffad1209365124'
podman_local=(env -u CONTAINER_HOST -u CONTAINER_CONNECTION podman --remote=false)
command -v podman >/dev/null
command -v timeout >/dev/null
if [[ $(id -u) == 0 || $("${podman_local[@]}" info --format '{{.Host.Security.Rootless}}') != true ]]; then
    printf '%s\n' 'Rootless local Podman and a non-root caller are required.' >&2
    exit 2
fi
"${podman_local[@]}" image exists "$image" || {
    printf '%s\n' 'The documented immutable fixture runtime must already be cached.' >&2
    exit 2
}
# No implicit pull, home/socket mounts, production paths, extra environment or
# writable host directory. The image supplies Python/GPG; /app is not invoked.
timeout --kill-after=5s 240s "${podman_local[@]}" run --rm --pull=never \
    --network none --read-only --read-only-tmpfs=false --cap-drop ALL \
    --security-opt no-new-privileges --user 10001:10001 \
    --pid private --ipc private --uts private --cgroupns private \
    --pids-limit 128 --memory 512m --memory-swap 512m --cpus 1 --timeout 230 \
    --ulimit nofile=1024:1024 --log-driver none \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=128m,mode=1777 --workdir /tmp \
    --mount "type=bind,source=$repo/scripts/resilience,destination=/fixture-scripts/resilience,ro" \
    --mount "type=bind,source=$repo/scripts/tests/test_resilience_local_fixture.py,destination=/fixture-scripts/tests/test_resilience_local_fixture.py,ro" \
    --mount "type=bind,source=$repo/scripts/tests/test_resilience_b2_preflight.py,destination=/fixture-scripts/tests/test_resilience_b2_preflight.py,ro" \
    --mount "type=bind,source=$repo/scripts/tests/test_resilience_b2_canary.py,destination=/fixture-scripts/tests/test_resilience_b2_canary.py,ro" \
    --mount "type=bind,source=$repo/scripts/tests/test_resilience_headscale_inventory.py,destination=/fixture-scripts/tests/test_resilience_headscale_inventory.py,ro" \
    --mount "type=bind,source=$repo/scripts/tests/test_resilience_headscale_fixture.py,destination=/fixture-scripts/tests/test_resilience_headscale_fixture.py,ro" \
    --entrypoint /bin/sh "$image" -ec '
        python3 -B -m unittest discover -s /fixture-scripts/tests -p "test_resilience_*.py" -v
        python3 -B /fixture-scripts/resilience/local_fixture.py
        python3 -B /fixture-scripts/resilience/headscale_fixture.py
    '
