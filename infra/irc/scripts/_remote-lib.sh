#!/usr/bin/env bash

info() { printf '[INFO] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
die() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }

validate_host() {
    local host="$1"
    [[ "$host" =~ ^[A-Za-z0-9._@:-]+$ ]] || die "Invalid SSH host: ${host}"
}

validate_remote_root() {
    local root="$1"
    [[ "$root" =~ ^/srv/[A-Za-z0-9._/-]+$ ]] || die "Remote root must be an absolute path below /srv"
}

require_local_command() {
    command -v "$1" >/dev/null 2>&1 || die "Required local command not found: $1"
}

remote_reachable() {
    ssh -o BatchMode=yes -o ConnectTimeout=8 "$1" true >/dev/null 2>&1
}

remote_stage_dir() {
    local host="$1" stage
    stage="$(ssh -o BatchMode=yes "$host" mktemp -d)" || return 1
    [[ "$stage" =~ ^/tmp/[^[:space:]]+$ ]] || die "Refusing unexpected remote staging path: ${stage}"
    printf '%s\n' "$stage"
}

cleanup_remote_stage() {
    local host="$1" stage="$2"
    [[ "$stage" =~ ^/tmp/[^[:space:]]+$ ]] || return 0
    ssh -o BatchMode=yes "$host" rm -rf -- "$stage" >/dev/null 2>&1 || true
}

require_remote_sudo() {
    ssh -o BatchMode=yes "$1" sudo -n true >/dev/null 2>&1
}

require_remote_docker_compose() {
    ssh -o BatchMode=yes "$1" \
        'command -v docker >/dev/null && (docker compose version >/dev/null 2>&1 || sudo -n docker compose version >/dev/null 2>&1)' \
        >/dev/null 2>&1
}
