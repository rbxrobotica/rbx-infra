#!/usr/bin/env bash
# Local, per-user preparation only. No secrets, accounts, services or MCP config are changed.
set -euo pipefail

agent="${1:-}"
if [[ "$agent" != local && "$agent" != thinkcentre ]]; then
    printf 'Usage: %s local|thinkcentre\n' "$0" >&2
    exit 1
fi

umask 077
source_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../bots/strategos-irc-bot" && pwd)"
python_bin="${RBX_IRC_PYTHON:-python3}"
install_root="${RBX_AGENT_IRC_INSTALL_ROOT:-$HOME/.local/share/rbx-agent-irc}"
config_root="${RBX_AGENT_IRC_CONFIG_ROOT:-$HOME/.config/rbx-agent-irc}"

if [[ "$install_root" != /* || "$config_root" != /* || -L "$install_root" || -L "$config_root" ]]; then
    printf 'Installation/configuration directories must be absolute and not symlinks.\n' >&2
    exit 1
fi

for path in "$install_root" "$config_root"; do
    if [[ -e "$path" && ( ! -d "$path" || ! -O "$path" ) ]]; then
        printf 'Refusing unowned or non-directory installation path.\n' >&2
        exit 1
    fi
done

install -d -m 0700 "$install_root/app" "$config_root"
# Install immutable source copies, not an editable link into a temporary worktree.
cp "$source_root/pyproject.toml" "$install_root/app/pyproject.toml"
cp -R "$source_root/src" "$install_root/app/"
if [[ ! -e "$config_root/agent.yaml" ]]; then
    install -m 0600 "$source_root/agent.${agent}.example.yaml" "$config_root/agent.yaml"
fi
install -m 0600 "$source_root/systemd/rbx-agent-irc.service.example" "$config_root/rbx-agent-irc.service.example"

if [[ ! -x "$install_root/venv/bin/python" ]]; then
    "$python_bin" -m venv "$install_root/venv"
fi
"$install_root/venv/bin/python" -m pip install "$install_root/app[agents]"
"$install_root/venv/bin/rbx-agent-irc" --config "$config_root/agent.yaml" status
printf 'Prepared %s. Review agent.yaml and the runbook before activation.\n' "$agent"
