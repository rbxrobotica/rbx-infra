#!/usr/bin/env bash
set -euo pipefail

# PowerDNS API credentials — read from pass, injected as provider-native env vars.
# The pan-net/powerdns provider reads PDNS_API_KEY and PDNS_SERVER_URL directly,
# bypassing the Terraform variable system (no tfvars precedence issues).
#
# Prerequisites:
#   1. SSH tunnel must be open:
#        ssh -o ExitOnForwardFailure=yes -f -N -L 127.0.0.1:18081:127.0.0.1:8081 root@149.102.139.33
#      If port 18081 is already in use (stale tunnel), kill it first:
#        pkill -f 'ssh.*18081'
#   2. pass entry must exist:
#        pass rbx/dns/pdns-api-key
#
# Usage:
#   ~/apps/rbx-infra/scripts/dns-tofu-env.sh ~/.local/bin/tofu plan
#   ~/apps/rbx-infra/scripts/dns-tofu-env.sh ~/.local/bin/tofu apply

export PDNS_API_KEY="$(pass rbx/dns/pdns-api-key)"
export PDNS_SERVER_URL="http://127.0.0.1:18081"

exec "$@"
