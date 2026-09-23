#!/usr/bin/env bash
# Decision-to-action E2E: FlightDeck -> Public Presence -> Maestro -> Corbetti -> Public Presence.
#
# Runs the four real codebases locally (Go services, the Flight Deck service
# layer under Bun, the real Corbetti executor scripts) with fakes ONLY at the
# external edges: the Claude Code CLI, gh, GitHub's contents API and the target
# repository (a local bare repo standing in for rbxrobotica/rbx-creatives).
# Nothing is published: Public Presence runs with its outbox worker disabled
# and the E2E asserts the item reached SCHEDULED with a planned publication.
#
# Do not run the repositories' own test suites while this runs: they reset the
# same disposable databases.
#
# Prerequisites (owner workstation): go, bun, psql, python3, jq, git, and the
# three podman Postgres containers used by the repositories' own tests:
#   flightdeck-db-test 127.0.0.1:5434, presence-db-test 127.0.0.1:5435,
#   maestro-db-test 127.0.0.1:5436 (created by this script if missing).
#
# Usage: E2E_FD_DIR=... E2E_PP_DIR=... E2E_MAESTRO_DIR=... E2E_INFRA_DIR=... bash run.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FD_DIR="${E2E_FD_DIR:-$HOME/rbx/.wt/fd-activation}"
PP_DIR="${E2E_PP_DIR:-$HOME/rbx/.wt/pp-activation}"
MAESTRO_DIR="${E2E_MAESTRO_DIR:-$HOME/rbx/.wt/maestro-activation}"
INFRA_DIR="${E2E_INFRA_DIR:-$(cd "${here}/../.." && pwd)}"
ROLE_FILES="${INFRA_DIR}/bootstrap/ansible/roles/agent-workbench/files"

work="$(mktemp -d /tmp/d2a-e2e.XXXXXX)"
log() { printf '[e2e %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
pids=()
started_containers=()
cleanup() {
  for p in "${pids[@]:-}"; do
    [[ -n "$p" ]] || continue
    pkill -P "$p" 2>/dev/null || true
    kill "$p" 2>/dev/null || true
  done
  # Stop only the database containers this run created or started; leave the
  # ones that were already running (the repositories' own test fixtures) alone.
  for c in "${started_containers[@]:-}"; do [[ -n "$c" ]] && podman stop -t 5 "$c" >/dev/null 2>&1 || true; done
  log "artifacts kept under ${work}"
}
trap cleanup EXIT

for port in 18080 18081; do
  if ss -ltn 2>/dev/null | grep -q ":${port} "; then
    echo "port ${port} is already in use; stop the other service first" >&2; exit 2
  fi
done

need() { command -v "$1" >/dev/null || { echo "missing tool: $1" >&2; exit 2; }; }
for t in go bun psql python3 jq git curl podman; do need "$t"; done

# ---------------------------------------------------------------- databases
ensure_db() { # name port user pass db
  if ! podman container exists "$1" 2>/dev/null; then
    podman run -d --name "$1" -p "127.0.0.1:$2:5432" -e POSTGRES_USER="$3" -e POSTGRES_PASSWORD="$4" -e POSTGRES_DB="$5" docker.io/library/postgres:16-alpine >/dev/null
    started_containers+=("$1")
  elif [[ "$(podman inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" != "true" ]]; then
    podman start "$1" >/dev/null
    started_containers+=("$1")
  fi
  for _ in $(seq 1 30); do pg_isready -h 127.0.0.1 -p "$2" -U "$3" >/dev/null 2>&1 && return 0; sleep 1; done
  echo "database $1 did not become ready" >&2; exit 2
}
ensure_db flightdeck-db-test 5434 flightdeck flightdeck_local_test flightdeck_test
ensure_db presence-db-test 5435 presence presence presence_test
ensure_db maestro-db-test 5436 maestro maestro maestro_test

FD_DB='postgres://flightdeck:flightdeck_local_test@127.0.0.1:5434/flightdeck_test'
PP_DB='postgres://presence:presence@127.0.0.1:5435/presence_test?sslmode=disable'
MAESTRO_DB='postgres://maestro:maestro@127.0.0.1:5436/maestro_test?sslmode=disable'

log "reset Maestro schema and apply migrations"
PGPASSWORD=maestro psql -q -h 127.0.0.1 -p 5436 -U maestro -d maestro_test -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;' >/dev/null
for f in $(ls "${MAESTRO_DIR}"/migrations/*.up.sql | sort); do
  PGPASSWORD=maestro psql -q -h 127.0.0.1 -p 5436 -U maestro -d maestro_test -v ON_ERROR_STOP=1 -f "$f" >/dev/null
done

log "reset Public Presence schema (the API applies its own migrations at start)"
PGPASSWORD=presence psql -q -h 127.0.0.1 -p 5435 -U presence -d presence_test -c 'DROP SCHEMA IF EXISTS presence CASCADE; DROP TABLE IF EXISTS schema_migrations;' >/dev/null 2>&1 || true

log "reset Flight Deck schema and apply migrations"
(cd "${FD_DIR}" && TEST_DATABASE_URL="${FD_DB}" bun -e "
import postgres from 'postgres';
const sql = postgres(process.env.TEST_DATABASE_URL, {max:1});
await sql.unsafe('drop schema public cascade; create schema public;');
await sql.end();" && TEST_DATABASE_URL="${FD_DB}" bun scripts/migrate.js --test >/dev/null)

# ---------------------------------------------------------------- target repository
log "build the synthetic rbxrobotica/rbx-creatives origin"
origin="${work}/origin.git"; src="${work}/source"
git init -q --bare "${origin}"; git init -q -b main "${src}"
git -C "${src}" config user.name e2e; git -C "${src}" config user.email e2e@example.invalid
mkdir -p "${src}/scripts" "${src}/missions"
cp "${here}/fakes/verify.ts" "${src}/scripts/verify.ts"
printf '{"name":"rbx-creatives-e2e","private":true,"type":"module"}\n' > "${src}/package.json"
touch "${src}/missions/.gitkeep"
git -C "${src}" add . && git -C "${src}" commit -qm "e2e base"
SOURCE_COMMIT="$(git -C "${src}" rev-parse HEAD)"
git -C "${src}" remote add origin "${origin}" && git -C "${src}" push -q -u origin main

# ---------------------------------------------------------------- fake GitHub contents API
E2E_BARE_REPO="${origin}" python3 "${here}/fakes/github-contents.py" 0 > "${work}/gh-port" 2>"${work}/gh.log" &
pids+=($!)
for _ in $(seq 1 30); do [[ -s "${work}/gh-port" ]] && break; sleep 0.2; done
GH_API="http://127.0.0.1:$(cat "${work}/gh-port")"

# ---------------------------------------------------------------- keys
ADMIT_KEY="e2e-flightdeck-admit-key-0123456789abcdef"
DISPATCH_KEY="e2e-flightdeck-dispatch-key-0123456789abcdef"
RUNNER_KEY="e2e-runner-key-0123456789abcdef"
PP_SERVICE_KEY="e2e-presence-service-key-0123456789abcdef"
PP_BFF_KEY="e2e-presence-bff-key-0123456789abcdefgh"

# ---------------------------------------------------------------- Maestro
# Build the two Go services into the work dir and run the binaries directly so
# that the recorded pid is the server itself (`go run` would leave a grandchild).
log "build Maestro and Public Presence"
(cd "${MAESTRO_DIR}" && go build -o "${work}/maestro-api" ./cmd/api)
(cd "${PP_DIR}/services/api" && go build -o "${work}/presence-api" ./cmd/api)

log "start Maestro"
(cd "${MAESTRO_DIR}" && DATABASE_URL="${MAESTRO_DB}" PORT=18080 AGENT_LOOP_AUTH=off \
  AGENT_LOOP_RUNNER_KEY="${RUNNER_KEY}" AGENT_LOOP_FLIGHTDECK_KEY="${ADMIT_KEY}" \
  AGENT_LOOP_FLIGHTDECK_DISPATCH_KEY="${DISPATCH_KEY}" AGENT_LOOP_FLIGHTDECK_ALLOWED_REPOS="rbxrobotica/rbx-creatives" \
  MAESTRO_ENVIRONMENT=test MAESTRO_DASHBOARD_KEY=e2e-dashboard exec "${work}/maestro-api" > "${work}/maestro.log" 2>&1) &
pids+=($!)
MAESTRO_URL="http://127.0.0.1:18080/api/v1/agent-loop"

# ---------------------------------------------------------------- Public Presence
log "start Public Presence (outbox worker off, fake GitHub)"
(cd "${PP_DIR}/services/api" && DATABASE_URL="${PP_DB}" PORT=18081 PRESENCE_ENV=development \
  PRESENCE_DOCTRINE_DIR=../../identities PRESENCE_ASSET_STORE=fs PRESENCE_FS_ASSET_DIR="${work}/assets" \
  PRESENCE_SERVICE_KEY="${PP_SERVICE_KEY}" PRESENCE_BFF_KEY="${PP_BFF_KEY}" PRESENCE_WORKER=off \
  PRESENCE_GITHUB_TOKEN=e2e-fake-token PRESENCE_GITHUB_API_BASE="${GH_API}" \
  PRESENCE_PUBLIC_BASE_URL=http://127.0.0.1:18081 exec "${work}/presence-api" serve > "${work}/presence.log" 2>&1) &
pids+=($!)
PP_URL="http://127.0.0.1:18081"

wait_http() { for _ in $(seq 1 90); do curl -fsS "$1" >/dev/null 2>&1 && return 0; sleep 1; done; echo "timeout waiting for $1" >&2; tail -20 "$2" >&2; exit 2; }
wait_http "http://127.0.0.1:18080/healthz" "${work}/maestro.log"
wait_http "${PP_URL}/readyz" "${work}/presence.log"

# Doctrine sync and migrations finish shortly after readiness; retry the first read.
PROFILE_ID=""
for _ in $(seq 1 30); do
  PROFILE_ID="$(curl -sS -H "Authorization: Bearer ${PP_SERVICE_KEY}" "${PP_URL}/api/v1/identities/psyctl/profiles" 2>/dev/null | jq -r '[.[]? | select(.channel_key=="instagram")][0].id // (.[0].id? // empty)' 2>/dev/null || true)"
  [[ "${PROFILE_ID}" =~ ^[0-9a-f-]{36}$ ]] && break
  sleep 1
done
[[ "${PROFILE_ID}" =~ ^[0-9a-f-]{36}$ ]] || { echo "could not resolve a psyctl profile id" >&2; exit 2; }
log "psyctl profile ${PROFILE_ID}"

# ---------------------------------------------------------------- Flight Deck driver
# The driver lives in the work dir and imports the Flight Deck sources by
# absolute path, so nothing is written into the Flight Deck checkout.
drive="${work}/flightdeck-drive.ts"
sed "s#@fd/#${FD_DIR}/#g" "${here}/flightdeck-drive.ts" > "${drive}"
fd() { (cd "${FD_DIR}" && TEST_DATABASE_URL="${FD_DB}" PP_URL="${PP_URL}" PP_SERVICE_KEY="${PP_SERVICE_KEY}" \
  MAESTRO_URL="http://127.0.0.1:18080" MAESTRO_ADMIT_KEY="${ADMIT_KEY}" MAESTRO_DISPATCH_KEY="${DISPATCH_KEY}" \
  MAESTRO_TARGET_ENVIRONMENT=test PP_PROFILE_ID="${PROFILE_ID}" E2E_REPO="rbxrobotica/rbx-creatives" \
  E2E_SOURCE_COMMIT="${SOURCE_COMMIT}" E2E_STATE="${work}/fd-state.json" bun run "${drive}" "$@"); }

log "seed Flight Deck (standing authorization + approved action)"; fd seed | tee "${work}/fd-seed.json"
run_stage() { # name; fails loudly when the stage reports an error code
  log "stage $1"; fd stage "$1" | tee "${work}/fd-$1.json"
  if jq -e '.result.errorCode? // empty' "${work}/fd-$1.json" >/dev/null 2>&1; then
    echo "stage $1 reported $(jq -r '.result.errorCode' "${work}/fd-$1.json")" >&2
    echo "--- last Public Presence log lines"; tail -5 "${work}/presence.log" >&2
    echo "--- last Maestro log lines"; tail -5 "${work}/maestro.log" >&2
    exit 1
  fi
}
for stage in source_event maestro_admission dispatch materialize activate; do run_stage "${stage}"; done
fd state | tee "${work}/fd-state-activated.json"
jq -e '.intent.state=="ACTIVATED" and (.approvals[0].decidedByKind=="policy")' "${work}/fd-state-activated.json" >/dev/null || { echo "expected ACTIVATED via a policy dispatch decision" >&2; exit 1; }
MISSION_CODE="$(jq -r '.intent.maestroMissionRef' "${work}/fd-state-activated.json")"

# ---------------------------------------------------------------- Corbetti (real executor, fake provider)
log "run the Corbetti executor for ${MISSION_CODE}"
export HOME="${work}/corbetti-home"; mkdir -p "${HOME}/rbx/repos/rbxrobotica" "${HOME}/rbx/runner" "${work}/fakebin"
git clone -q --bare "${origin}" "${HOME}/rbx/repos/rbxrobotica/rbx-creatives.git"
cp "${here}/fakes/claude" "${here}/fakes/gh" "${work}/fakebin/"; chmod +x "${work}/fakebin/"*
export PATH="${work}/fakebin:${PATH}" RUNNER_ID=corbetti-e2e RUNNER_GIT_AUTHOR_NAME="RBX E2E Runner" RUNNER_GIT_AUTHOR_EMAIL="runner@example.invalid"
export AGENT_LOOP_RUNNER_KEY="${RUNNER_KEY}" MAESTRO_URL GITHUB_PAT=e2e-fake-pat E2E_REPO="rbxrobotica/rbx-creatives"
claim="$(curl -fsS -H "Authorization: Bearer ${RUNNER_KEY}" -H "X-Runner-Id: ${RUNNER_ID}" "${MAESTRO_URL}/leases/next")"
[[ -n "${claim}" ]] || { echo "runner got no lease (204)" >&2; exit 1; }
echo "${claim}" > "${work}/claim.json"
jq '.contract' "${work}/claim.json" > "${work}/contract.json"
bash "${ROLE_FILES}/rbx-mission-executor.sh" "${MISSION_CODE}" "${work}/contract.json" \
  "$(jq -r '.lease.id' "${work}/claim.json")" "$(jq -r '.lease.claim_token' "${work}/claim.json")" "$(jq -r '.lease.claim_generation' "${work}/claim.json")" \
  > "${work}/executor.log" 2>&1 || { echo "executor failed"; tail -40 "${work}/executor.log"; exit 1; }
jq -e '.delivery.outcome=="delivered" and .delivery.capabilities.design.mode=="degraded" and (.delivery.design|not)' "${HOME}/rbx/manifests/${MISSION_CODE}/result.json" >/dev/null \
  || { echo "unexpected runner result"; cat "${HOME}/rbx/manifests/${MISSION_CODE}/result.json"; exit 1; }
unset HOME; export HOME="$(getent passwd "$(id -u)" | cut -d: -f6)"

# ---------------------------------------------------------------- reconcile + import + autonomous continuation
run_stage reconcile
fd state | tee "${work}/fd-state-terminal.json"
jq -e '.intent.state=="TERMINAL" and .intent.terminalOutcome=="delivered"' "${work}/fd-state-terminal.json" >/dev/null || { echo "expected TERMINAL delivered" >&2; exit 1; }

JOB_ID="$(jq -r '.intent.jobId' "${work}/fd-state-terminal.json")"
ITEM_ID="$(jq -r '.intent.publicPresenceContentItemId' "${work}/fd-state-terminal.json")"
job="$(curl -fsS -H "Authorization: Bearer ${PP_SERVICE_KEY}" "${PP_URL}/api/v1/creative-jobs/${JOB_ID}")"
echo "${job}" > "${work}/pp-job.json"
jq -e '.status=="done"' "${work}/pp-job.json" >/dev/null || { echo "creative job not done"; cat "${work}/pp-job.json"; exit 1; }
item="$(curl -fsS -H "Authorization: Bearer ${PP_SERVICE_KEY}" "${PP_URL}/api/v1/content/${ITEM_ID}")"
echo "${item}" > "${work}/pp-item.json"
jq -e '.lifecycle_status=="SCHEDULED"' "${work}/pp-item.json" >/dev/null || { echo "item not SCHEDULED (autonomous continuation)"; cat "${work}/pp-item.json"; exit 1; }
pubs="$(PGPASSWORD=presence psql -tA -h 127.0.0.1 -p 5435 -U presence -d presence_test -c "select count(*) from presence.publications where content_item_id='${ITEM_ID}' and status='scheduled'")"
[[ "${pubs}" == "1" ]] || { echo "expected exactly one scheduled publication, got ${pubs}" >&2; exit 1; }

log "E2E PASSED: policy dispatch -> activation -> Corbetti delivery (degraded design, no attestation) -> import -> QA -> autonomous approval -> SCHEDULED publication (worker off, nothing published)"
