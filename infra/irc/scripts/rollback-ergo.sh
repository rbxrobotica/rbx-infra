#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_remote-lib.sh"

HOST="${RBX_IRC_HOST:-corbetti}"
REMOTE_DIR="${RBX_IRC_REMOTE_ROOT:-/srv/rbx/irc}/ergo"

[[ "${RBX_IRC_CONFIRM:-}" == "rollback-ergo" ]] || die "Set RBX_IRC_CONFIRM=rollback-ergo to stop and remove only the Ergo container"
validate_host "$HOST"
validate_remote_root "$REMOTE_DIR"
remote_reachable "$HOST" || die "SSH host ${HOST} is unavailable"
require_remote_sudo "$HOST" || die "Passwordless sudo is required for rollback"

ssh -o BatchMode=yes "$HOST" sudo -n bash -s -- "$REMOTE_DIR" <<'REMOTE'
set -euo pipefail
remote_dir="$1"
if [[ -f "$remote_dir/compose.yaml" && -f "$remote_dir/.env" ]]; then
    cd "$remote_dir"
    docker compose --env-file .env -f compose.yaml rm --stop --force ergo
else
    docker rm -f rbx-ergo 2>/dev/null || true
fi
printf '[OK] Ergo container removed; data under %s/config was preserved.\n' "$remote_dir"
REMOTE
