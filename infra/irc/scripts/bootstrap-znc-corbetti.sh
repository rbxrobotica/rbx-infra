#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_remote-lib.sh"

HOST="${RBX_IRC_HOST:-corbetti}"
REMOTE_DIR="${RBX_IRC_REMOTE_ROOT:-/srv/rbx/irc}/znc"
SOURCE_DIR="${SCRIPT_DIR}/../znc"
stage=""

manual_steps() {
    cat >&2 <<EOF
Manual deployment:
  Copy ${SOURCE_DIR} to ${HOST}:${REMOTE_DIR}
  sudo install -d -m 0700 ${REMOTE_DIR}/config
  sudo chown 1000:1000 ${REMOTE_DIR}/config
  cd ${REMOTE_DIR}
  sudo cp -n env.example .env && sudo chmod 0600 .env
  sudo docker compose --env-file .env -f compose.yaml config --quiet
  sudo docker compose --env-file .env -f compose.yaml up -d znc
  sudo ss -ltnp | grep ':6501'
EOF
}

validate_host "$HOST"
validate_remote_root "$REMOTE_DIR"
require_local_command ssh
require_local_command scp

if ! remote_reachable "$HOST"; then
    warn "SSH host ${HOST} is unavailable."
    manual_steps
    exit 2
fi
if ! require_remote_sudo "$HOST"; then
    warn "Passwordless sudo is required for automated deployment to /srv."
    manual_steps
    exit 3
fi
if ! require_remote_docker_compose "$HOST"; then
    warn "Docker Compose is unavailable for ${HOST}."
    manual_steps
    exit 4
fi

stage="$(remote_stage_dir "$HOST")"
trap 'cleanup_remote_stage "$HOST" "$stage"' EXIT
scp -q \
    "${SOURCE_DIR}/compose.yaml" \
    "${SOURCE_DIR}/env.example" \
    "${SOURCE_DIR}/README.md" \
    "${HOST}:${stage}/"

info "Installing ZNC artifacts on ${HOST}:${REMOTE_DIR}"
ssh -o BatchMode=yes "$HOST" sudo -n bash -s -- "$stage" "$REMOTE_DIR" <<'REMOTE'
set -euo pipefail
stage="$1"
remote_dir="$2"

install -d -m 0750 "$remote_dir"
install -d -m 0700 "$remote_dir/config"
install -m 0644 "$stage/compose.yaml" "$remote_dir/compose.yaml"
install -m 0644 "$stage/env.example" "$remote_dir/env.example"
install -m 0644 "$stage/README.md" "$remote_dir/README.md"
if [[ ! -e "$remote_dir/.env" ]]; then
    install -m 0600 "$stage/env.example" "$remote_dir/.env"
else
    chmod 0600 "$remote_dir/.env"
    printf '[SKIP] Preserving existing %s/.env\n' "$remote_dir"
fi
puid="$(sed -n 's/^PUID=\([0-9][0-9]*\)$/\1/p' "$remote_dir/.env" | tail -1)"
pgid="$(sed -n 's/^PGID=\([0-9][0-9]*\)$/\1/p' "$remote_dir/.env" | tail -1)"
puid="${puid:-1000}"
pgid="${pgid:-1000}"
chown "$puid:$pgid" "$remote_dir/config"
chmod 0700 "$remote_dir/config"

cd "$remote_dir"
docker compose --env-file .env -f compose.yaml config --quiet
docker compose --env-file .env -f compose.yaml up -d znc
docker compose --env-file .env -f compose.yaml ps znc

port="$(sed -n 's/^ZNC_PORT=\([0-9][0-9]*\)$/\1/p' .env | tail -1)"
port="${port:-6501}"
listeners=""
for _attempt in {1..15}; do
    listeners="$(ss -ltnH "sport = :${port}" || true)"
    [[ -n "$listeners" ]] && break
    sleep 1
done
if grep -Eq '(^|[[:space:]])(0\.0\.0\.0|\*|\[::\]):' <<<"$listeners"; then
    printf '[ERROR] ZNC has a public wildcard bind on port %s.\n' "$port" >&2
    exit 1
fi
if ! grep -Eq "127\\.0\\.0\\.1:${port}([[:space:]]|$)" <<<"$listeners"; then
    printf '[ERROR] Expected ZNC on 127.0.0.1:%s; observed:\n%s\n' "$port" "$listeners" >&2
    exit 1
fi
printf '[OK] ZNC is bound only to loopback on 127.0.0.1:%s\n' "$port"
REMOTE

cat <<EOF
[OK] ZNC bootstrap complete.
Open a tunnel with:
  RBX_IRC_HOST=${HOST} ${SCRIPT_DIR}/open-znc-tunnel.sh
Then open http://127.0.0.1:6501 and rotate the initial admin password immediately.
EOF
