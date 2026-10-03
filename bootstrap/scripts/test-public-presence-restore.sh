#!/usr/bin/env bash
# Read-only remote backup drill in an ephemeral PostgreSQL 16 container.
set -euo pipefail

if [ "$#" -ne 4 ]; then
    echo "Usage: $0 <ssh-host> <remote-rclone-config> <remote:bucket/jaguar/postgres> <YYYY-MM-DDTHHMMSSZ>" >&2
    exit 2
fi
SOURCE_HOST=$1
RCLONE_CONFIG=$2
REMOTE=${3%/}
STAMP=$4
if [[ ! $STAMP =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{6}Z$ ]]; then
    echo 'Invalid backup stamp' >&2
    exit 2
fi
if [[ ! $SOURCE_HOST =~ ^[A-Za-z0-9_.-]+$ || ! $RCLONE_CONFIG =~ ^/[A-Za-z0-9_./-]+$ || ! $REMOTE =~ ^[A-Za-z0-9_:./-]+$ ]]; then
    echo 'Invalid source host or remote path' >&2
    exit 2
fi
NAME="rbx-presence-restore-$$"
cleanup() { podman rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

# PostgreSQL uses its Unix socket; this disposable container has no network.
podman run -d --rm --pull=never --name "$NAME" --network none \
    --tmpfs /var/lib/postgresql/data:rw,size=1g \
    -e POSTGRES_HOST_AUTH_METHOD=trust postgres:16 >/dev/null
ready=0
for _ in $(seq 1 30); do
    if podman exec -u postgres "$NAME" pg_isready -q; then ready=1; break; fi
    sleep 1
done
if [ "$ready" -ne 1 ]; then echo 'PostgreSQL did not start' >&2; exit 1; fi

# A new cluster already contains the bootstrap postgres role.
ssh -n -o BatchMode=yes "$SOURCE_HOST" \
    "rclone --config $RCLONE_CONFIG cat $REMOTE/$STAMP/globals.sql.gz" \
    | gzip -dc \
    | sed '/^CREATE ROLE postgres;$/d' \
    | podman exec -i -u postgres "$NAME" psql -v ON_ERROR_STOP=1 -d postgres >/dev/null
podman exec -u postgres "$NAME" createdb -O public_presence public_presence
ssh -n -o BatchMode=yes "$SOURCE_HOST" \
    "rclone --config $RCLONE_CONFIG cat $REMOTE/$STAMP/public_presence.dump" \
    | podman exec -i -u postgres "$NAME" pg_restore --exit-on-error -d public_presence

podman exec -u postgres "$NAME" psql -v ON_ERROR_STOP=1 -Atc \
    "SELECT 'role=' || EXISTS (SELECT 1 FROM pg_roles WHERE rolname='public_presence') ||
            ' tables=' || count(*) FROM pg_tables WHERE schemaname='public'" \
    -d public_presence
echo "OK isolated_public_presence_restore=$STAMP"
