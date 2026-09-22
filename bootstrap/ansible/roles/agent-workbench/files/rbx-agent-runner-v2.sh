#!/usr/bin/env bash
# Corbetti transport loop: claim -> fenced heartbeat -> one-shot mission
# executor. Repository and provider behavior live behind separate boundaries.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
set -a
# shellcheck source=.env
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/.env"
set +a

POLL_INTERVAL_S="${POLL_INTERVAL_S:-30}"
HEARTBEAT_INTERVAL_S="${HEARTBEAT_INTERVAL_S:-60}"
STATE_DIR="${HOME}/.rbx/watchdog"
CONTRACT_DIR="${HOME}/rbx/contracts"
ACTIVE_MISSION_FILE="${STATE_DIR}/active-mission"
BUDGET_STOP_FILE="${STATE_DIR}/budget-stop"
MISSION_EXECUTOR="${SCRIPT_DIR}/rbx-mission-executor.sh"

export PATH="${HOME}/rbx/bin:${HOME}/.local/bin:${HOME}/.kimi-code/bin:${HOME}/rbx/.devbox/nix/profile/default/bin:${HOME}/rbx/.devbox/npm-global/bin:${PATH}"
export GH_TOKEN="${GITHUB_PAT}"
git config --global credential.helper '!gh auth git-credential' 2>/dev/null || true

mkdir -p "${STATE_DIR}" "${CONTRACT_DIR}" "${HOME}/rbx/logs" "${HOME}/rbx/manifests"

ts()  { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
log() { echo "[$(ts)] [${RUNNER_ID}] $*"; }

poll_next() {
  curl -sS \
    -H "Authorization: Bearer ${AGENT_LOOP_RUNNER_KEY}" \
    -H "X-Runner-Id: ${RUNNER_ID}" \
    -w '\n%{http_code}' \
    "${MAESTRO_URL}/leases/next"
}

heartbeat_loop() {
  local code="$1" lease_id="$2" claim_token="$3" payload http_code curl_rc
  payload="$(jq -n --arg lease_id "${lease_id}" --arg claim_token "${claim_token}" \
    '{lease_id:$lease_id,claim_token:$claim_token}')"
  while true; do
    sleep "${HEARTBEAT_INTERVAL_S}"
    set +e
    http_code="$(curl -sS -o /dev/null -w '%{http_code}' -XPOST \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer ${AGENT_LOOP_RUNNER_KEY}" \
      -H "X-Runner-Id: ${RUNNER_ID}" \
      --data-binary "${payload}" \
      "${MAESTRO_URL}/missions/${code}/lease/heartbeat")"
    curl_rc=$?
    set -e
    if [[ ${curl_rc} -ne 0 || "${http_code}" != 2[0-9][0-9] ]]; then
      log "WARN fenced heartbeat failed for ${code} (curl=${curl_rc}, HTTP=${http_code:-000})"
    fi
  done
}

log "rbx-agent-runner starting (runner=${RUNNER_ID}, maestro=${MAESTRO_URL})"

while true; do
  if [[ -f "${BUDGET_STOP_FILE}" ]]; then
    log "BUDGET PAUSE marker present; refusing new claims pending operator review"
    sleep "${POLL_INTERVAL_S}"
    continue
  fi

  set +e
  raw="$(poll_next 2>/dev/null)"
  poll_rc=$?
  set -e
  if [[ ${poll_rc} -ne 0 ]]; then
    log "WARN network error polling /leases/next; retrying"
    sleep "${POLL_INTERVAL_S}"
    continue
  fi
  http_code="$(tail -1 <<<"${raw}")"
  body="$(sed '$d' <<<"${raw}")"

  case "${http_code}" in
    204)
      sleep "${POLL_INTERVAL_S}"
      ;;
    200)
      mission_code="$(jq -er '.mission_code' <<<"${body}")"
      lease_id="$(jq -er '.lease.id' <<<"${body}")"
      claim_token="$(jq -er '.lease.claim_token' <<<"${body}")"
      claim_generation="$(jq -er '.lease.claim_generation' <<<"${body}")"
      contract_file="${CONTRACT_DIR}/${mission_code}.json"
      jq -e '.contract' <<<"${body}" >"${contract_file}"

      log "Claimed ${mission_code} lease=${lease_id} generation=${claim_generation}"
      printf '%s|%s|%s\n' "$$" "${mission_code}" "${lease_id}" >"${ACTIVE_MISSION_FILE}"

      heartbeat_loop "${mission_code}" "${lease_id}" "${claim_token}" &
      hb_pid=$!
      mission_rc=0
      "${MISSION_EXECUTOR}" "${mission_code}" "${contract_file}" "${lease_id}" "${claim_token}" "${claim_generation}" || mission_rc=$?
      kill "${hb_pid}" 2>/dev/null || true
      wait "${hb_pid}" 2>/dev/null || true
      rm -f "${ACTIVE_MISSION_FILE}"

      if [[ ${mission_rc} -ne 0 ]]; then
        log "ERROR ${mission_code}: one-shot executor exited ${mission_rc}; lease remains fenced for retry/reclaim"
      fi
      ;;
    *)
      log "WARN unexpected HTTP ${http_code} from /leases/next"
      sleep "${POLL_INTERVAL_S}"
      ;;
  esac
done
