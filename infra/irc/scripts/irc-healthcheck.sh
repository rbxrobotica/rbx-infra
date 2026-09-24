#!/usr/bin/env bash
set -u

HOST="${RBX_IRC_HOST:-corbetti}"
ROOT="${RBX_IRC_REMOTE_ROOT:-/srv/rbx/irc}"
failures=0

ok() { printf '[OK] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*"; failures=$((failures + 1)); }

if command -v tmux >/dev/null 2>&1; then ok 'tmux installed'; else warn 'tmux not installed'; fi
if command -v weechat >/dev/null 2>&1; then ok 'weechat installed'; else warn 'weechat not installed'; fi

if [[ ! "$HOST" =~ ^[A-Za-z0-9._@:-]+$ ]]; then
    fail "invalid SSH host: ${HOST}"
elif ! ssh -o BatchMode=yes -o ConnectTimeout=8 "$HOST" true >/dev/null 2>&1; then
    warn "cannot reach ${HOST}; remote checks skipped"
else
    ssh -o BatchMode=yes "$HOST" bash -s -- "$ROOT" <<'REMOTE'
set -u
root="$1"
remote_failures=0
ok() { printf '[OK] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*"; remote_failures=$((remote_failures + 1)); }

if command -v docker >/dev/null 2>&1; then
    ok 'docker installed on IRC host'
else
    fail 'docker missing on IRC host'
fi

check_container() {
    local name="$1" label="$2"
    if docker inspect "$name" >/dev/null 2>&1; then
        if [[ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null)" == true ]]; then
            ok "$label container running"
        else
            fail "$label container exists but is not running"
        fi
    else
        warn "$label container not deployed"
    fi
}

check_file() {
    local path="$1" label="$2"
    if [[ -f "$path" ]]; then
        ok "$label exists"
        mode="$(stat -c '%a' "$path" 2>/dev/null || true)"
        if [[ "$mode" =~ ^[0-7]{3,4}$ ]] && ((8#$mode & 077)); then
            fail "$label is accessible to group/other users (mode $mode)"
        fi
    else
        warn "$label is absent"
    fi
}

check_dir() {
    local path="$1" label="$2"
    if [[ -d "$path" ]]; then
        mode="$(stat -c '%a' "$path" 2>/dev/null || true)"
        if [[ "$mode" =~ ^[0-7]{3,4}$ ]] && ((8#$mode & 077)); then
            fail "$label is accessible to group/other users (mode $mode)"
        else
            ok "$label permissions are restricted"
        fi
    else
        warn "$label is absent"
    fi
}

check_container rbx-znc znc
check_container rbx-ergo ergo
check_file "$root/znc/.env" 'ZNC .env'
check_file "$root/ergo/.env" 'Ergo .env'
check_file "$root/ergo/config/ircd.yaml" 'Ergo final config'
check_dir "$root/znc/config" 'ZNC config directory'
check_dir "$root/ergo/config" 'Ergo config directory'
if [[ -f "$root/znc/.env" && -d "$root/znc/config" ]]; then
    expected_uid="$(sed -n 's/^PUID=\([0-9][0-9]*\)$/\1/p' "$root/znc/.env" | tail -1)"
    expected_gid="$(sed -n 's/^PGID=\([0-9][0-9]*\)$/\1/p' "$root/znc/.env" | tail -1)"
    actual_owner="$(stat -c '%u:%g' "$root/znc/config" 2>/dev/null || true)"
    if [[ "$actual_owner" == "${expected_uid:-1000}:${expected_gid:-1000}" ]]; then
        ok 'ZNC config ownership matches PUID:PGID'
    else
        fail "ZNC config ownership mismatch (${actual_owner:-unknown})"
    fi
fi
if [[ -f "$root/ergo/config/ircd.yaml" ]]; then
    if grep -Eq '^[[:space:]]*exempted:[[:space:]]*\[\][[:space:]]*$' "$root/ergo/config/ircd.yaml"; then
        ok 'Ergo has no SASL account exemptions'
    else
        fail 'Ergo SASL exemption is active; close the account-bootstrap window'
    fi
fi

listeners="$(ss -ltnH 2>/dev/null || true)"
for port in 6501 6667 6697; do
    port_lines="$(awk -v port=":${port}" '$4 ~ port "$" {print}' <<<"$listeners")"
    if [[ -z "$port_lines" ]]; then
        warn "nothing listening on IRC port $port"
        continue
    fi
    if awk '{print $4}' <<<"$port_lines" | grep -Eq '^(0\.0\.0\.0|\*|\[::\]):'; then
        fail "IRC port $port is bound to a wildcard address"
    else
        ok "IRC port $port has no wildcard bind"
    fi
    if [[ "$port" == 6501 || "$port" == 6667 ]]; then
        if grep -Eq "127\\.0\\.0\\.1:${port}([[:space:]]|$)" <<<"$port_lines"; then
            ok "port $port is bound to loopback"
        else
            fail "port $port must remain bound to 127.0.0.1"
        fi
    elif [[ "$port" == 6697 ]]; then
        while read -r local_address; do
            bind_address="${local_address%:${port}}"
            if [[ "$bind_address" =~ ^127\.0\.0\.1$ || \
                  "$bind_address" =~ ^10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ || \
                  "$bind_address" =~ ^192\.168\.[0-9]{1,3}\.[0-9]{1,3}$ || \
                  "$bind_address" =~ ^172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3}$ || \
                  "$bind_address" =~ ^100\.(6[4-9]|[78][0-9]|9[0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
                ok "TLS port 6697 is bound to approved private address $bind_address"
            else
                fail "TLS port 6697 is bound outside approved private ranges: $bind_address"
            fi
        done < <(awk '{print $4}' <<<"$port_lines")
    fi
done

if ((remote_failures > 0)); then exit 1; fi
REMOTE
    remote_status=$?
    if ((remote_status != 0)); then failures=$((failures + 1)); fi
fi

if ((failures > 0)); then
    printf '[FAIL] Healthcheck completed with %d failure(s).\n' "$failures" >&2
    exit 1
fi
printf '[OK] Healthcheck completed with no detected unsafe state.\n'
