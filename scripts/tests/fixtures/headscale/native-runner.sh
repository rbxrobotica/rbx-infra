#!/bin/sh
# Only synthetic state in the isolated container's tmpfs. Never run on a host.
set -eu
umask 077
[ "${RBX_HEADSCALE_SYNTHETIC_SANDBOX:-}" = 1 ] || exit 90
[ ! -w /etc/headscale/config.yaml ] || exit 91
/usr/local/bin/headscale version | grep -qx 'headscale version v0.29.3'
mkdir -p /var/lib/headscale /run/headscale /tmp/home
hs() { /usr/local/bin/headscale --config /fixtures/bootstrap.yaml "$@"; }

# This deny-all bootstrap serves only to create synthetic identities. It never
# supplies the verdict for the actual, byte-preserved candidate below.
hs serve >/tmp/seed-daemon.log 2>&1 &
daemon=$!
trap 'kill "$daemon" 2>/dev/null || true; wait "$daemon" 2>/dev/null || true' EXIT HUP INT TERM
ready=false
for unused in $(seq 1 100); do
    if [ -S /run/headscale/headscale.sock ]; then ready=true; break; fi
    kill -0 "$daemon" 2>/dev/null || exit 92
    sleep 0.1
done
[ "$ready" = true ] || exit 93
hs users create admin
hs users create phone
hs debug create-node --name admin-client --user admin --key hskey-authreq-aaaaaaaaaaaaaaaaaaaaaaaa
hs auth register --user admin --auth-id hskey-authreq-aaaaaaaaaaaaaaaaaaaaaaaa
hs debug create-node --name phone-client --user phone --key hskey-authreq-bbbbbbbbbbbbbbbbbbbbbbbb
hs auth register --user phone --auth-id hskey-authreq-bbbbbbbbbbbbbbbbbbbbbbbb
hs debug create-node --name target --user admin --key hskey-authreq-cccccccccccccccccccccccc
hs auth register --user admin --auth-id hskey-authreq-cccccccccccccccccccccccc
kill "$daemon"
daemon_status=0
wait "$daemon" || daemon_status=$?
case "$daemon_status" in 0|143) ;; *) exit 95 ;; esac
trap - EXIT HUP INT TERM

# configtest opens/migrates SQLite and creates a Noise key; all paths here are
# tmpfs. This is the complete prepared candidate, not the bootstrap config.
/usr/local/bin/headscale --config /etc/headscale/config.yaml configtest
/usr/local/bin/headscale --config /etc/headscale/config.yaml policy check \
    --file /etc/headscale/policy.hujson --bypass-grpc-and-access-database-directly --force
printf '%s\n' RBX_NATIVE_CONFIG_AND_MATRIX_PASS

# A zero exit from these checks is a test failure. The daemon is already stopped,
# so direct database access is exclusive and restricted to synthetic tmpfs state.
for invalid in invalid-schema missing-identity reversed-ports; do
    [ -r "/fixtures/$invalid.json" ] || exit 96
    if /usr/local/bin/headscale --config /etc/headscale/config.yaml policy check \
        --file "/fixtures/$invalid.json" --bypass-grpc-and-access-database-directly --force \
        >"/tmp/$invalid.log" 2>&1; then
        exit 94
    fi
done
printf '%s\n' RBX_NATIVE_NEGATIVES_PASS
