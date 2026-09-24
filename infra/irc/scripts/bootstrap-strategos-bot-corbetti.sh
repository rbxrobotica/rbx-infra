#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_remote-lib.sh"

HOST="${RBX_IRC_HOST:-corbetti}"
REMOTE_DIR="${RBX_IRC_REMOTE_ROOT:-/srv/rbx/irc}/bots/strategos-irc-bot"
SOURCE_DIR="${SCRIPT_DIR}/../bots/strategos-irc-bot"
stage=""

validate_host "$HOST"
validate_remote_root "$REMOTE_DIR"
require_local_command ssh
require_local_command scp
remote_reachable "$HOST" || die "SSH host ${HOST} is unavailable"
require_remote_sudo "$HOST" || die "Passwordless sudo is required for deployment to /srv"

stage="$(remote_stage_dir "$HOST")"
trap 'cleanup_remote_stage "$HOST" "$stage"' EXIT
scp -q \
    "${SOURCE_DIR}/README.md" \
    "${SOURCE_DIR}/config.example.yaml" \
    "${SOURCE_DIR}/pyproject.toml" \
    "${HOST}:${stage}/"
scp -q -r "${SOURCE_DIR}/src" "${SOURCE_DIR}/systemd" "${HOST}:${stage}/"

ssh -o BatchMode=yes "$HOST" sudo -n bash -s -- "$stage" "$REMOTE_DIR" <<'REMOTE'
set -euo pipefail
stage="$1"
remote_dir="$2"
app_dir="$remote_dir/app"
unit=/etc/systemd/system/strategos-irc-bot.service

command -v python3 >/dev/null || { printf '[SAFE STOP] python3 is required.\n' >&2; exit 20; }
python3 -c 'import yaml' >/dev/null 2>&1 || {
    printf '[SAFE STOP] PyYAML is required (Ubuntu package: python3-yaml).\n' >&2
    exit 21
}
if [[ ! -s "$remote_dir/bot.env" ]]; then
    install -d -m 0700 "$remote_dir"
    printf '[SAFE STOP] Create %s/bot.env with STRATEGOS_IRC_PASSWORD, mode 0600.\n' "$remote_dir" >&2
    exit 22
fi

install -d -m 0755 "$remote_dir" "$app_dir"
install -m 0644 "$stage/README.md" "$app_dir/README.md"
install -m 0644 "$stage/pyproject.toml" "$app_dir/pyproject.toml"
rm -rf -- "$app_dir/src"
cp -a "$stage/src" "$app_dir/src"
find "$app_dir/src" -type d -exec chmod 0755 {} +
find "$app_dir/src" -type f -exec chmod 0644 {} +
install -m 0644 "$stage/config.example.yaml" "$remote_dir/config.example.yaml"
if [[ ! -e "$remote_dir/config.yaml" ]]; then
    install -m 0644 "$stage/config.example.yaml" "$remote_dir/config.yaml"
else
    printf '[SKIP] Preserving existing %s/config.yaml\n' "$remote_dir"
fi
chmod 0600 "$remote_dir/bot.env"
install -m 0644 "$stage/systemd/strategos-irc-bot.service.example" "$unit"

systemd-analyze verify "$unit"
systemctl daemon-reload
systemctl enable --now strategos-irc-bot.service
sleep 2
systemctl is-active --quiet strategos-irc-bot.service
systemctl --no-pager --full status strategos-irc-bot.service | sed -n '1,20p'
printf '[OK] Strategos IRC bot is active under systemd hardening.\n'
REMOTE
