#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${SCRIPT_DIR}/../weechat"
TARGET_DIR="${HOME}/.config/rbx-irc"
HELPER="${HOME}/.local/bin/rbx-irc"

if [[ ! -r /etc/os-release ]]; then
    printf '[ERROR] Cannot identify the operating system (/etc/os-release missing).\n' >&2
    exit 1
fi

# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID:-}" != "linuxmint" && " ${ID_LIKE:-} " != *" debian "* && " ${ID_LIKE:-} " != *" ubuntu "* ]]; then
    printf '[ERROR] Supported only on Linux Mint, Ubuntu, or Debian; detected %s.\n' "${PRETTY_NAME:-unknown}" >&2
    exit 1
fi

packages=(tmux weechat ca-certificates netcat-openbsd)
missing=()
for package in "${packages[@]}"; do
    if ! dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'ok installed'; then
        missing+=("$package")
    fi
done

if ((${#missing[@]} > 0)); then
    if ((EUID == 0)); then
        apt=(apt-get)
    elif command -v sudo >/dev/null 2>&1; then
        apt=(sudo apt-get)
    else
        printf '[ERROR] Packages missing (%s) and sudo is unavailable.\n' "${missing[*]}" >&2
        exit 1
    fi
    if [[ "${RBX_IRC_APT_UPDATE:-1}" == "1" ]]; then
        "${apt[@]}" update
    fi
    "${apt[@]}" install -y --no-install-recommends "${missing[@]}"
else
    printf '[OK] Required packages are already installed.\n'
fi

install -d -m 0700 "$TARGET_DIR"
mkdir -p "$(dirname "$HELPER")"
for example in \
    weechat-bootstrap.commands.example \
    servers-public.commands.example \
    servers-znc.commands.example \
    servers-ergo.commands.example; do
    if [[ -e "${TARGET_DIR}/${example}" ]]; then
        printf '[SKIP] Preserving existing %s\n' "${TARGET_DIR}/${example}"
    else
        install -m 0600 "${SOURCE_DIR}/${example}" "${TARGET_DIR}/${example}"
        printf '[OK] Installed %s\n' "${TARGET_DIR}/${example}"
    fi
done

if [[ -e "$HELPER" ]]; then
    printf '[SKIP] Preserving existing helper %s\n' "$HELPER"
else
    helper_tmp="$(mktemp)"
    trap 'rm -f "$helper_tmp"' EXIT
    {
        printf '%s\n' '#!/usr/bin/env bash'
        printf '%s\n' 'set -euo pipefail'
        printf '%s\n' 'exec tmux new-session -A -s irc weechat'
    } >"$helper_tmp"
    install -m 0755 "$helper_tmp" "$HELPER"
    printf '[OK] Installed helper %s\n' "$HELPER"
fi

printf '\nReady. Start with: %s\n' "$HELPER"
printf 'Detach: Ctrl-b d | Reattach: tmux attach -t irc\n'
printf 'Examples: %s\n' "$TARGET_DIR"
