#!/usr/bin/env bash
set -euo pipefail

HOST="${RBX_IRC_HOST:-corbetti}"
LOCAL_BIND="${LOCAL_ZNC_BIND:-127.0.0.1}"
LOCAL_PORT="${LOCAL_ZNC_PORT:-6501}"
REMOTE_PORT="${REMOTE_ZNC_PORT:-6501}"

[[ "$HOST" =~ ^[A-Za-z0-9._@:-]+$ ]] || { printf '[ERROR] Invalid SSH host.\n' >&2; exit 1; }
[[ "$LOCAL_BIND" == "127.0.0.1" ]] || { printf '[ERROR] LOCAL_ZNC_BIND must remain 127.0.0.1.\n' >&2; exit 1; }
for port in "$LOCAL_PORT" "$REMOTE_PORT"; do
    if [[ ! "$port" =~ ^[0-9]+$ ]] || ((10#$port < 1 || 10#$port > 65535)); then
        printf '[ERROR] Invalid port: %s\n' "$port" >&2
        exit 1
    fi
done

printf '[INFO] Forwarding %s:%s to %s:127.0.0.1:%s\n' "$LOCAL_BIND" "$LOCAL_PORT" "$HOST" "$REMOTE_PORT"
printf '[INFO] Stop the tunnel with Ctrl-C.\n'
exec ssh -N -T \
    -o BatchMode=yes \
    -o ExitOnForwardFailure=yes \
    -L "${LOCAL_BIND}:${LOCAL_PORT}:127.0.0.1:${REMOTE_PORT}" \
    "$HOST"
