#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_remote-lib.sh"

HOST="${RBX_IRC_HOST:-corbetti}"
REMOTE_DIR="${RBX_IRC_REMOTE_ROOT:-/srv/rbx/irc}/ergo"
SOURCE_DIR="${SCRIPT_DIR}/../ergo"
ALLOW_ACCOUNT_BOOTSTRAP="${RBX_IRC_ACCOUNT_BOOTSTRAP:-0}"
stage=""

manual_steps() {
    cat >&2 <<EOF
Manual preparation:
  Copy ${SOURCE_DIR} to ${HOST}:${REMOTE_DIR}
  sudo install -d -m 0700 ${REMOTE_DIR}/config/tls
  cd ${REMOTE_DIR}
  sudo cp -n env.example .env && sudo chmod 0600 .env
  sudo cp -n ircd.yaml.example config/ircd.yaml
  Replace the demonstration oper hash and provision config/tls/{fullchain.pem,privkey.pem}.
  Re-run this script, or validate and start with docker compose.
EOF
}

validate_host "$HOST"
validate_remote_root "$REMOTE_DIR"
[[ "$ALLOW_ACCOUNT_BOOTSTRAP" == "0" || "$ALLOW_ACCOUNT_BOOTSTRAP" == "1" ]] || die "RBX_IRC_ACCOUNT_BOOTSTRAP must be 0 or 1"
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
    "${SOURCE_DIR}/ergo.motd.example" \
    "${SOURCE_DIR}/ircd.yaml.example" \
    "${SOURCE_DIR}/README.md" \
    "${HOST}:${stage}/"

info "Installing Ergo artifacts on ${HOST}:${REMOTE_DIR}"
if ssh -o BatchMode=yes "$HOST" sudo -n bash -s -- "$stage" "$REMOTE_DIR" "$ALLOW_ACCOUNT_BOOTSTRAP" <<'REMOTE'
set -euo pipefail
stage="$1"
remote_dir="$2"
allow_account_bootstrap="$3"
config_dir="$remote_dir/config"

install -d -m 0750 "$remote_dir"
install -d -m 0700 "$config_dir" "$config_dir/tls"
install -m 0644 "$stage/compose.yaml" "$remote_dir/compose.yaml"
install -m 0644 "$stage/env.example" "$remote_dir/env.example"
install -m 0644 "$stage/ergo.motd.example" "$remote_dir/ergo.motd.example"
install -m 0644 "$stage/ircd.yaml.example" "$remote_dir/ircd.yaml.example"
install -m 0644 "$stage/README.md" "$remote_dir/README.md"
if [[ ! -e "$remote_dir/.env" ]]; then
    install -m 0600 "$stage/env.example" "$remote_dir/.env"
else
    chmod 0600 "$remote_dir/.env"
    printf '[SKIP] Preserving existing %s/.env\n' "$remote_dir"
fi

if [[ ! -f "$config_dir/ircd.yaml" ]]; then
    printf '[SAFE STOP] Final config is absent. Review and run:\n' >&2
    printf '  sudo cp -n %s/ircd.yaml.example %s/ircd.yaml\n' "$remote_dir" "$config_dir" >&2
    printf 'Then replace the oper hash, provision TLS files, and rerun bootstrap.\n' >&2
    exit 20
fi
if [[ ! -e "$config_dir/ergo.motd" ]]; then
    install -m 0644 "$stage/ergo.motd.example" "$config_dir/ergo.motd"
fi
if grep -Fq '$2a$04$0123456789abcdef0123456789abcdef0123456789abcdef01234' "$config_dir/ircd.yaml"; then
    printf '[SAFE STOP] Refusing the known demonstration operator hash.\n' >&2
    exit 21
fi
for tls_file in fullchain.pem privkey.pem; do
    if [[ ! -s "$config_dir/tls/$tls_file" ]]; then
        printf '[SAFE STOP] Missing external TLS file: %s/tls/%s\n' "$config_dir" "$tls_file" >&2
        exit 22
    fi
done
if ! grep -Eq '^[[:space:]]*exempted:[[:space:]]*\[\][[:space:]]*$' "$config_dir/ircd.yaml"; then
    if [[ "$allow_account_bootstrap" != "1" ]]; then
        printf '[SAFE STOP] SASL exemptions must be empty outside the initial account bootstrap.\n' >&2
        printf 'Use RBX_IRC_ACCOUNT_BOOTSTRAP=1 only for the short SAREGISTER window.\n' >&2
        exit 24
    fi
    printf '[WARN] Temporary account-bootstrap exemption is active; remove it immediately after SAREGISTER.\n' >&2
fi
chmod 0600 "$config_dir/ircd.yaml" "$config_dir/tls/privkey.pem"

cd "$remote_dir"
tls_bind="$(sed -n 's/^ERGO_TLS_BIND_ADDRESS=\([^[:space:]]*\)$/\1/p' .env | tail -1)"
tls_bind="${tls_bind:-127.0.0.1}"
if [[ ! "$tls_bind" =~ ^127\.0\.0\.1$ && \
      ! "$tls_bind" =~ ^10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ && \
      ! "$tls_bind" =~ ^192\.168\.[0-9]{1,3}\.[0-9]{1,3}$ && \
      ! "$tls_bind" =~ ^172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3}$ && \
      ! "$tls_bind" =~ ^100\.(6[4-9]|[78][0-9]|9[0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
    printf '[SAFE STOP] Ergo TLS bind must be loopback, RFC1918, or Tailscale/CGNAT IPv4: %s\n' "$tls_bind" >&2
    exit 23
fi
docker compose --env-file .env -f compose.yaml config --quiet
docker compose --env-file .env -f compose.yaml run --rm --no-deps \
    --entrypoint /ircd-bin/ergo ergo run --conf /ircd/ircd.yaml --smoke
docker compose --env-file .env -f compose.yaml up -d ergo
docker compose --env-file .env -f compose.yaml ps ergo

irc_port="$(sed -n 's/^ERGO_IRC_PORT=\([0-9][0-9]*\)$/\1/p' .env | tail -1)"
ircs_port="$(sed -n 's/^ERGO_IRCS_PORT=\([0-9][0-9]*\)$/\1/p' .env | tail -1)"
irc_port="${irc_port:-6667}"
ircs_port="${ircs_port:-6697}"
listeners=""
for _attempt in {1..15}; do
    listeners="$(ss -ltnH "sport = :${irc_port}" || true)"
    [[ -n "$listeners" ]] && break
    sleep 1
done
if ! grep -Eq "127\\.0\\.0\\.1:${irc_port}([[:space:]]|$)" <<<"$listeners"; then
    printf '[ERROR] Plaintext Ergo must bind 127.0.0.1:%s; observed:\n%s\n' "$irc_port" "$listeners" >&2
    exit 1
fi
tls_listeners="$(ss -ltnH "sport = :${ircs_port}" || true)"
if awk '{print $4}' <<<"$tls_listeners" | grep -Eq '^(0\.0\.0\.0|\*|\[::\]):'; then
    printf '[ERROR] Ergo TLS has a public wildcard bind; refusing.\n' >&2
    exit 1
fi
if [[ -z "$tls_listeners" ]]; then
    printf '[ERROR] Ergo TLS is not listening on port %s.\n' "$ircs_port" >&2
    exit 1
fi
printf '[OK] Ergo plaintext is loopback-only and TLS has no wildcard bind.\n'
REMOTE
then
    :
else
    status=$?
    if [[ $status -ge 20 && $status -le 24 ]]; then
        manual_steps
    fi
    exit "$status"
fi

printf '[OK] Ergo bootstrap complete on %s.\n' "$HOST"
