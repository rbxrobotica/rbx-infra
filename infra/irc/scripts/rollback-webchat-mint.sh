#!/usr/bin/env bash
set -euo pipefail

[[ "${RBX_IRC_CONFIRM:-}" == 'rollback-webchat' ]] || {
    printf '[ERROR] Set RBX_IRC_CONFIRM=rollback-webchat to stop only the local webchat.\n' >&2
    exit 1
}

quadlet="${HOME}/.config/containers/systemd/rbx-irc-webchat.container"
disabled_quadlet="${quadlet}.disabled"
systemctl --user stop rbx-irc-webchat.service
if [[ -e "$quadlet" ]]; then
    [[ ! -e "$disabled_quadlet" ]] || {
        printf '[ERROR] Refusing to overwrite %s\n' "$disabled_quadlet" >&2
        exit 1
    }
    mv "$quadlet" "$disabled_quadlet"
fi
systemctl --user daemon-reload
if ss -ltnH | awk '$4 ~ /:9000$/ {print $4}' | grep -q .; then
    printf '[ERROR] A listener remains on port 9000; inspect it manually.\n' >&2
    exit 1
fi
printf '[OK] Local webchat stopped and disabled. Data, credentials and image were preserved; Quadlet moved to %s.\n' "$disabled_quadlet"
