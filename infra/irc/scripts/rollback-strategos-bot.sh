#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_remote-lib.sh"

HOST="${RBX_IRC_HOST:-corbetti}"

[[ "${RBX_IRC_CONFIRM:-}" == "rollback-strategos-bot" ]] || die "Set RBX_IRC_CONFIRM=rollback-strategos-bot to stop and disable the bot"
validate_host "$HOST"
remote_reachable "$HOST" || die "SSH host ${HOST} is unavailable"
require_remote_sudo "$HOST" || die "Passwordless sudo is required for rollback"

ssh -o BatchMode=yes "$HOST" sudo -n bash -s <<'REMOTE'
set -euo pipefail
systemctl disable --now strategos-irc-bot.service 2>/dev/null || true
printf '[OK] Strategos IRC bot stopped and disabled; app, config, and credentials were preserved.\n'
REMOTE
