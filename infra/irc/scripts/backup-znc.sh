#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_remote-lib.sh"

HOST="${RBX_IRC_HOST:-corbetti}"
ROOT="${RBX_IRC_REMOTE_ROOT:-/srv/rbx/irc}"
REMOTE_DIR="${ROOT}/znc"
BACKUP_DIR="${RBX_IRC_BACKUP_DIR:-${ROOT}/backups}"

validate_host "$HOST"
validate_remote_root "$REMOTE_DIR"
validate_remote_root "$BACKUP_DIR"
remote_reachable "$HOST" || die "SSH host ${HOST} is unavailable"
require_remote_sudo "$HOST" || die "Passwordless sudo is required for backup"

ssh -o BatchMode=yes "$HOST" sudo -n bash -s -- "$REMOTE_DIR" "$BACKUP_DIR" <<'REMOTE'
set -euo pipefail
remote_dir="$1"
backup_dir="$2"
[[ -d "$remote_dir/config" ]] || { printf '[ERROR] ZNC config directory is absent.\n' >&2; exit 1; }
install -d -m 0700 "$backup_dir"
was_running=false
if docker inspect -f '{{.State.Running}}' rbx-znc 2>/dev/null | grep -qx true; then
    was_running=true
fi
restart() {
    if [[ "$was_running" == true ]]; then
        cd "$remote_dir"
        docker compose --env-file .env -f compose.yaml start znc >/dev/null
    fi
}
trap restart EXIT
if [[ "$was_running" == true ]]; then
    cd "$remote_dir"
    docker compose --env-file .env -f compose.yaml stop znc >/dev/null
fi
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="$backup_dir/znc-$stamp.tar.gz"
tar -C / -czf "$archive" "${remote_dir#/}/config" "${remote_dir#/}/.env" "${remote_dir#/}/compose.yaml"
chmod 0600 "$archive"
printf '[OK] Backup created: %s\n' "$archive"
REMOTE
