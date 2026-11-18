#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${SCRIPT_DIR}/../webchat"
DATA_DIR="${RBX_IRC_WEBCHAT_DATA_DIR:-${HOME}/.local/share/rbx-irc/thelounge}"
CONFIG_DIR="${RBX_IRC_WEBCHAT_CONFIG_DIR:-${HOME}/.config/rbx-irc}"
QUADLET_DIR="${HOME}/.config/containers/systemd"
QUADLET_FILE="${QUADLET_DIR}/rbx-irc-webchat.container"
DISABLED_QUADLET_FILE="${QUADLET_FILE}.disabled"
CREDENTIALS_FILE="${CONFIG_DIR}/webchat-initial-credentials"
WEBCHAT_USER="${RBX_IRC_WEBCHAT_USER:-leandro}"
IMAGE='ghcr.io/thelounge/thelounge:4.5.2@sha256:b19c2ef06efce2a494a7f142e5acba2db3086a591453f25916e204d342e3d194'

die() {
    printf '[ERROR] %s\n' "$*" >&2
    exit 1
}

[[ "$WEBCHAT_USER" =~ ^[A-Za-z0-9_-]{1,32}$ ]] || die 'Invalid webchat user name'
[[ -r /etc/os-release ]] || die 'Cannot identify the operating system'
# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID:-}" != 'linuxmint' && " ${ID_LIKE:-} " != *' ubuntu '* && " ${ID_LIKE:-} " != *' debian '* ]]; then
    die "Supported only on Linux Mint, Ubuntu, or Debian; detected ${PRETTY_NAME:-unknown}"
fi
command -v podman >/dev/null 2>&1 || die 'Podman is required; refusing to install another container engine'
command -v openssl >/dev/null 2>&1 || die 'openssl is required to generate the initial local password'
command -v curl >/dev/null 2>&1 || die 'curl is required for the loopback health check'

install -d -m 0700 "$DATA_DIR" "$DATA_DIR/users" "$DATA_DIR/logs" "$DATA_DIR/packages"
install -d -m 0700 "$CONFIG_DIR" "$QUADLET_DIR"

if [[ -e "$DATA_DIR/config.js" ]]; then
    grep -Eq '^[[:space:]]*public:[[:space:]]*false,' "$DATA_DIR/config.js" || die 'Existing config is not private; refusing to start'
    grep -Eq '^[[:space:]]*host:[[:space:]]*"127\.0\.0\.1",' "$DATA_DIR/config.js" || die 'Existing config is not loopback-only; refusing to start'
    printf '[SKIP] Preserving existing %s\n' "$DATA_DIR/config.js"
else
    install -m 0600 "$SOURCE_DIR/config.js" "$DATA_DIR/config.js"
    printf '[OK] Installed loopback-only webchat configuration.\n'
fi

if [[ ! -e "$QUADLET_FILE" && -e "$DISABLED_QUADLET_FILE" ]]; then
    cmp -s "$SOURCE_DIR/rbx-irc-webchat.container" "$DISABLED_QUADLET_FILE" || \
        die "Existing ${DISABLED_QUADLET_FILE} differs; review it instead of overwriting"
    mv "$DISABLED_QUADLET_FILE" "$QUADLET_FILE"
    printf '[OK] Re-enabled the preserved Quadlet unit.\n'
elif [[ -e "$QUADLET_FILE" ]]; then
    cmp -s "$SOURCE_DIR/rbx-irc-webchat.container" "$QUADLET_FILE" || \
        die "Existing ${QUADLET_FILE} differs; review it instead of overwriting"
else
    install -m 0600 "$SOURCE_DIR/rbx-irc-webchat.container" "$QUADLET_FILE"
    printf '[OK] Installed rootless Quadlet unit.\n'
fi

if podman image exists "$IMAGE"; then
    printf '[SKIP] Pinned webchat image already exists locally.\n'
else
    podman pull "$IMAGE" >/dev/null
fi

if [[ ! -f "$DATA_DIR/users/${WEBCHAT_USER}.json" ]]; then
    if [[ ! -f "$CREDENTIALS_FILE" ]]; then
        password="$(openssl rand -base64 32 | tr -d '\n')"
        credentials_tmp="$(mktemp)"
        trap 'rm -f "$credentials_tmp"' EXIT
        {
            printf 'WEBCHAT_USERNAME=%s\n' "$WEBCHAT_USER"
            printf 'WEBCHAT_PASSWORD=%s\n' "$password"
        } >"$credentials_tmp"
        install -m 0600 "$credentials_tmp" "$CREDENTIALS_FILE"
    else
        stored_user="$(sed -n 's/^WEBCHAT_USERNAME=//p' "$CREDENTIALS_FILE" | tail -1)"
        [[ "$stored_user" == "$WEBCHAT_USER" ]] || die 'Credential file belongs to a different user'
        password="$(sed -n 's/^WEBCHAT_PASSWORD=//p' "$CREDENTIALS_FILE" | tail -1)"
        [[ -n "$password" ]] || die 'Credential file has no password'
    fi
    # The CLI has no stdin/file password option. Keep the generated secret out
    # of logs; it exists in argv only for this short, local, networkless process.
    podman run --rm --network none \
        --userns=keep-id:uid=1000,gid=1000 \
        -v "$DATA_DIR:/var/opt/thelounge:Z" \
        "$IMAGE" add --password "$password" "$WEBCHAT_USER" >/dev/null
    unset password
    printf '[OK] Created private webchat user; credentials are in %s (0600).\n' "$CREDENTIALS_FILE"
else
    printf '[SKIP] Preserving existing webchat user %s.\n' "$WEBCHAT_USER"
fi

if ss -ltnH | awk '$4 ~ /:9000$/ {print $4}' | grep -qv '^127\.0\.0\.1:9000$'; then
    die 'Port 9000 has a non-loopback listener; refusing to continue'
fi

systemctl --user daemon-reload
# Quadlet [Install] dependencies are generated during daemon-reload. Generated
# units cannot be enabled with systemctl; starting is sufficient here and the
# generated default.target.wants link handles future user-manager starts.
systemctl --user start rbx-irc-webchat.service

for _ in {1..30}; do
    if curl --fail --silent --max-time 2 http://127.0.0.1:9000/ >/dev/null; then
        break
    fi
    sleep 1
done
curl --fail --silent --show-error --max-time 3 http://127.0.0.1:9000/ >/dev/null || \
    die 'Webchat did not pass its loopback HTTP health check'

listeners="$(ss -ltnH | awk '$4 ~ /:9000$/ {print $4}')"
[[ "$listeners" == '127.0.0.1:9000' ]] || die "Unsafe or unexpected port 9000 listener: ${listeners:-none}"
printf '[OK] Webchat is private and healthy at http://127.0.0.1:9000\n'
printf '[INFO] No IRC credential was copied; enter the individual Ergo SASL credential in the web UI.\n'
