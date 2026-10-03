#!/usr/bin/env bash
# Jaguar PostgreSQL dump set to the dedicated, locked off-site backup bucket.
set -euo pipefail

STAMP=$(date -u +%Y-%m-%dT%H%M%SZ)
ROOT=/var/backups/postgres-protected
DIR="$ROOT/$STAMP"
RCLONE_CONFIG=/root/.config/rclone/postgres-backup.conf
REMOTE=postgres-backup:rbx-postgres-recovery-eu2/jaguar/postgres
ASSET_SOURCE=s3:rbx-presence
ASSET_REMOTE=postgres-backup:rbx-postgres-recovery-eu2/jaguar/presence-assets
ASSET_STATUS_REMOTE=postgres-backup:rbx-postgres-recovery-eu2/jaguar/presence-assets-status
METRIC_DIR=/var/lib/node-exporter/textfile

combined_config=$(mktemp /run/rclone-backup.XXXXXX)
metric_tmp=
cleanup() {
    rm -f "$combined_config"
    if [ -n "$metric_tmp" ]; then rm -f "$metric_tmp"; fi
}
trap cleanup EXIT
chmod 600 "$combined_config"
cat /root/.config/rclone/rclone.conf "$RCLONE_CONFIG" > "$combined_config"

mkdir -p "$DIR"
chmod 700 "$ROOT" "$DIR"

sudo -u postgres pg_dumpall --globals-only | gzip > "$DIR/globals.sql.gz"
dbs=$(sudo -u postgres psql -tAc "SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY 1")
if [ -z "$dbs" ]; then
    echo 'ERROR no databases returned by PostgreSQL' >&2
    exit 1
fi
while IFS= read -r db; do
    # The root-owned backup directory is 0700; root intentionally redirects stdout.
    # shellcheck disable=SC2024
    sudo -u postgres pg_dump -Fc "$db" > "$DIR/$db.dump"
done <<< "$dbs"

rclone --config "$RCLONE_CONFIG" copy --immutable "$DIR" "$REMOTE/$STAMP"
rclone --config "$RCLONE_CONFIG" check "$DIR" "$REMOTE/$STAMP"

# The source bucket is versioned. Snapshot its current objects into the locked
# backup bucket and record even an empty snapshot as an immutable manifest.
rclone --config "$combined_config" copy --immutable "$ASSET_SOURCE" "$ASSET_REMOTE/$STAMP"
rclone --config "$combined_config" check "$ASSET_SOURCE" "$ASSET_REMOTE/$STAMP"
assets_count=$(rclone --config "$combined_config" size "$ASSET_SOURCE" --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["count"])')
printf 'timestamp=%s\nobjects=%s\n' "$STAMP" "$assets_count" > "$DIR/assets-manifest.txt"
rclone --config "$RCLONE_CONFIG" copyto --immutable "$DIR/assets-manifest.txt" "$ASSET_STATUS_REMOTE/$STAMP/manifest.txt"

metric_tmp=$(mktemp "$METRIC_DIR/.rbx_postgres_backup.XXXXXX")
printf 'rbx_postgres_backup_last_success_timestamp_seconds %s\n' "$(date +%s)" > "$metric_tmp"
chmod 644 "$metric_tmp"
mv "$metric_tmp" "$METRIC_DIR/rbx_postgres_backup.prom"
metric_tmp=

find "$ROOT" -mindepth 1 -maxdepth 1 -type d -mtime +2 -exec rm -rf {} +
echo "OK $STAMP protected_backup_files=$(find "$DIR" -maxdepth 1 -type f | wc -l)"
