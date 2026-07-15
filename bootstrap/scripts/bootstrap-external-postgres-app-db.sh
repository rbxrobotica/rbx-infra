#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  bootstrap-external-postgres-app-db.sh \
    --namespace <k8s-namespace> \
    --secret <k8s-secret-name> \
    --key <database-url-key> \
    --expected-db <postgres-db> \
    --expected-user <postgres-role> \
    --hba-ip <node-ip> [--hba-ip <node-ip> ...]

Options:
  --postgres-host <host>       SSH host for the Postgres server. Default: jaguar
  --postgres-version <version> PostgreSQL config version. Default: 16
  --dry-run                    Validate inputs and print planned non-secret actions.

Environment:
  RBX_KUBECTL                  Command used to read the Kubernetes Secret.
                               Default: "rtk kubectl"
  RBX_SSH                      Command used to SSH to the Postgres server.
                               Default: "rtk ssh"

This script never prints the database password. It reads DATABASE_URL from the
existing Kubernetes Secret, aligns the Postgres role password to that value,
creates the database when missing, adds scoped pg_hba.conf entries for the
provided node IPs, and reloads Postgres config.
USAGE
}

fail() {
  echo "error: $*" >&2
  exit 1
}

namespace=""
secret_name=""
secret_key=""
expected_db=""
expected_user=""
postgres_host="jaguar"
postgres_version="16"
dry_run=0
hba_ips=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace)
      namespace="${2:-}"
      shift 2
      ;;
    --secret)
      secret_name="${2:-}"
      shift 2
      ;;
    --key)
      secret_key="${2:-}"
      shift 2
      ;;
    --expected-db)
      expected_db="${2:-}"
      shift 2
      ;;
    --expected-user)
      expected_user="${2:-}"
      shift 2
      ;;
    --postgres-host)
      postgres_host="${2:-}"
      shift 2
      ;;
    --postgres-version)
      postgres_version="${2:-}"
      shift 2
      ;;
    --hba-ip)
      hba_ips+=("${2:-}")
      shift 2
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      fail "unknown argument: $1"
      ;;
  esac
done

[[ -n "$namespace" ]] || fail "--namespace is required"
[[ -n "$secret_name" ]] || fail "--secret is required"
[[ -n "$secret_key" ]] || fail "--key is required"
[[ -n "$expected_db" ]] || fail "--expected-db is required"
[[ -n "$expected_user" ]] || fail "--expected-user is required"
[[ ${#hba_ips[@]} -gt 0 ]] || fail "at least one --hba-ip is required"
[[ "$postgres_version" =~ ^[0-9]+$ ]] || fail "--postgres-version must be numeric"

RBX_KUBECTL=${RBX_KUBECTL:-"rtk kubectl"}
RBX_SSH=${RBX_SSH:-"rtk ssh"}

secret_template="{{ index .data \"$secret_key\" }}"
# shellcheck disable=SC2086
database_url_b64="$($RBX_KUBECTL get secret -n "$namespace" "$secret_name" -o "go-template=${secret_template}")"
[[ -n "$database_url_b64" ]] || fail "secret key not found: ${namespace}/${secret_name}:${secret_key}"

database_url="$(printf '%s' "$database_url_b64" | base64 -d)"
parsed="$(
  DATABASE_URL="$database_url" python3 - <<'PY'
import os
import shlex
import sys
from urllib.parse import unquote, urlparse

url = os.environ["DATABASE_URL"]
parsed = urlparse(url)
if parsed.scheme not in ("postgres", "postgresql"):
    sys.exit("DATABASE_URL must use postgres:// or postgresql://")
if not parsed.username or not parsed.password or not parsed.hostname or not parsed.path.strip("/"):
    sys.exit("DATABASE_URL must include username, password, host, and database")

values = {
    "DB_USER": unquote(parsed.username),
    "DB_PASSWORD": unquote(parsed.password),
    "DB_HOST": parsed.hostname,
    "DB_PORT": str(parsed.port or 5432),
    "DB_NAME": parsed.path.lstrip("/").split("?", 1)[0],
}
for key, value in values.items():
    print(f"{key}={shlex.quote(value)}")
PY
)"
eval "$parsed"

[[ "$DB_USER" == "$expected_user" ]] || fail "secret user '$DB_USER' does not match expected user '$expected_user'"
[[ "$DB_NAME" == "$expected_db" ]] || fail "secret database '$DB_NAME' does not match expected database '$expected_db'"
[[ "$DB_USER" =~ ^[A-Za-z0-9_]+$ ]] || fail "database user contains unsupported characters"
[[ "$DB_NAME" =~ ^[A-Za-z0-9_]+$ ]] || fail "database name contains unsupported characters"

normalized_hba=()
for ip in "${hba_ips[@]}"; do
  [[ -n "$ip" ]] || fail "empty --hba-ip value"
  [[ "$ip" =~ ^[0-9A-Fa-f:.]+(/[0-9]+)?$ ]] || fail "invalid --hba-ip value: $ip"
  if [[ "$ip" == */* ]]; then
    normalized_hba+=("$ip")
  elif [[ "$ip" == *:* ]]; then
    normalized_hba+=("${ip}/128")
  else
    normalized_hba+=("${ip}/32")
  fi
done

if [[ "$dry_run" -eq 1 ]]; then
  echo "dry-run: Kubernetes Secret ${namespace}/${secret_name}:${secret_key}"
  echo "dry-run: parsed host=${DB_HOST} port=${DB_PORT} db=${DB_NAME} user=${DB_USER} password redacted"
  echo "dry-run: Postgres host=${postgres_host} pg_hba=/etc/postgresql/${postgres_version}/main/pg_hba.conf"
  for cidr in "${normalized_hba[@]}"; do
    echo "dry-run: would ensure pg_hba line: host ${DB_NAME} ${DB_USER} ${cidr} scram-sha-256"
  done
  exit 0
fi

password_sql="${DB_PASSWORD//\'/\'\'}"

{
  printf "DO \\$\\$ BEGIN\n"
  printf "  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '%s') THEN\n" "$DB_USER"
  printf "    CREATE ROLE %s LOGIN PASSWORD '%s';\n" "$DB_USER" "$password_sql"
  printf "  ELSE\n"
  printf "    ALTER ROLE %s WITH LOGIN PASSWORD '%s';\n" "$DB_USER" "$password_sql"
  printf "  END IF;\n"
  printf "END \\$\\$;\n"
  printf "SELECT 'CREATE DATABASE %s OWNER %s' WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '%s')\\gexec\n" "$DB_NAME" "$DB_USER" "$DB_NAME"
  printf "GRANT ALL PRIVILEGES ON DATABASE %s TO %s;\n" "$DB_NAME" "$DB_USER"
  printf "\\connect %s\n" "$DB_NAME"
  printf "GRANT CREATE,USAGE ON SCHEMA public TO %s;\n" "$DB_USER"
  printf "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO %s;\n" "$DB_USER"
  printf "GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO %s;\n" "$DB_USER"
} | {
  # shellcheck disable=SC2086
  $RBX_SSH "$postgres_host" -- sudo -u postgres psql -v ON_ERROR_STOP=1
}

hba_path="/etc/postgresql/${postgres_version}/main/pg_hba.conf"
backup_path="${hba_path}.bak-external-postgres-$(date -u +%Y%m%dT%H%M%SZ)"
# shellcheck disable=SC2086
$RBX_SSH "$postgres_host" -- sudo cp -a "$hba_path" "$backup_path"
echo "created pg_hba backup: ${postgres_host}:${backup_path}"

for cidr in "${normalized_hba[@]}"; do
  line="host ${DB_NAME} ${DB_USER} ${cidr} scram-sha-256"
  remote_cmd="if sudo grep -qxF '$line' '$hba_path'; then echo 'exists $line'; else printf '%s\n' '$line' | sudo tee -a '$hba_path' >/dev/null && echo 'added $line'; fi"
  # shellcheck disable=SC2086
  $RBX_SSH "$postgres_host" -- "$remote_cmd"
done

# shellcheck disable=SC2086
$RBX_SSH "$postgres_host" -- sudo -u postgres psql -Atc "SELECT pg_reload_conf();" >/dev/null
echo "reloaded PostgreSQL configuration on ${postgres_host}"
echo "done: db=${DB_NAME} user=${DB_USER} password redacted"
