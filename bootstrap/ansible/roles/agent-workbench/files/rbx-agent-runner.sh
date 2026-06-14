#!/usr/bin/env bash
# rbx-agent-runner — Corbetti workbench runner (ADR-0009 Phase 3)
#
# Poll loop: GET /leases/next → execute agent in isolated worktree →
# report terminal state (delivered | stopped) back to maestro.
#
# Sources: ~/rbx/runner/.env (written by Ansible agent-workbench role)
# Logs:    ~/rbx/logs/<mission_code>.log

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=.env
source "${SCRIPT_DIR}/.env"

POLL_INTERVAL_S=30
HEARTBEAT_INTERVAL_S=60
LOG_DIR="${HOME}/rbx/logs"
WORKTREE_DIR="${HOME}/rbx/worktrees"
REPOS_DIR="${HOME}/rbx/repos"

# Devbox tools take precedence (claude, codex, gh, etc.)
export PATH="${HOME}/.local/bin:${HOME}/rbx/.devbox/nix/profile/default/bin:${HOME}/rbx/.devbox/npm-global/bin:${PATH}"

# GitHub HTTPS auth via GH_TOKEN → git credential helper
export GH_TOKEN="${GITHUB_PAT}"
git config --global credential.helper '!gh auth git-credential' 2>/dev/null || true

mkdir -p "${LOG_DIR}"

ts()  { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
log() { echo "[$(ts)] [${RUNNER_ID}] $*"; }

# ── maestro HTTP helpers ────────────────────────────────────────────────────

poll_next() {
  curl -sf \
    -H "Authorization: Bearer ${AGENT_LOOP_RUNNER_KEY}" \
    -H "X-Runner-Id: ${RUNNER_ID}" \
    -w '\n%{http_code}' \
    "${MAESTRO_URL}/leases/next"
}

maestro_post() {
  local path="$1"
  curl -sf -XPOST \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${AGENT_LOOP_RUNNER_KEY}" \
    -H "X-Runner-Id: ${RUNNER_ID}" \
    -d @- \
    "${MAESTRO_URL}${path}" >/dev/null 2>&1 || true
}

heartbeat_loop() {
  local code="$1"
  while true; do
    sleep "${HEARTBEAT_INTERVAL_S}"
    echo '{}' | maestro_post "/missions/${code}/lease/heartbeat"
  done
}

report_stop() {
  local code="$1" reason="$2"
  printf '{"state":"stopped","stop_reason":"%s"}' "${reason}" \
    | maestro_post "/missions/${code}/lease/state"
  log "STOP ${code}: ${reason}"
}

report_delivered() {
  local code="$1"
  echo '{"state":"delivered"}' | maestro_post "/missions/${code}/lease/state"
  log "DELIVERED ${code}"
}

# ── mission execution ────────────────────────────────────────────────────────

execute_mission() {
  local code="$1" contract_json="$2"
  local log_file="${LOG_DIR}/${code}.log"
  local worktree="${WORKTREE_DIR}/${code}"

  # Parse contract
  local mtype repo base_branch objective max_runtime_iso
  mtype=$(printf '%s' "${contract_json}" | jq -r '.type')
  repo=$(printf '%s' "${contract_json}" | jq -r '.repo')
  base_branch=$(printf '%s' "${contract_json}" | jq -r '.base_branch // "main"')
  objective=$(printf '%s' "${contract_json}" | jq -r '.objective')
  max_runtime_iso=$(printf '%s' "${contract_json}" | jq -r '.max_runtime // "PT10M"')

  # Parse ISO 8601 duration to seconds (support PTxM and PTxH)
  local timeout_s=600
  if [[ "${max_runtime_iso}" =~ PT([0-9]+)M ]]; then
    timeout_s=$(( ${BASH_REMATCH[1]} * 60 ))
  elif [[ "${max_runtime_iso}" =~ PT([0-9]+)H ]]; then
    timeout_s=$(( ${BASH_REMATCH[1]} * 3600 ))
  fi

  log "START ${code} type=${mtype} repo=${repo} timeout=${timeout_s}s"
  {
    echo "=== mission ${code} === $(ts)"
    printf 'type: %s\nrepo: %s\nbranch: %s\nobjective: %s\n---\n' \
      "${mtype}" "${repo}" "${base_branch}" "${objective}"
  } >> "${log_file}"

  # ── clone / update bare repo ─────────────────────────────────────────────
  local org repo_name repo_dir
  org="${repo%%/*}"
  repo_name="${repo##*/}"
  repo_dir="${REPOS_DIR}/${org}/${repo_name}.git"
  mkdir -p "${REPOS_DIR}/${org}"

  if [[ ! -d "${repo_dir}" ]]; then
    log "Cloning ${repo}"
    git clone --bare "https://github.com/${repo}.git" "${repo_dir}" \
      >>"${log_file}" 2>&1
  else
    log "Fetching ${repo}"
    git -C "${repo_dir}" fetch origin >>"${log_file}" 2>&1
  fi

  # ── isolated worktree ────────────────────────────────────────────────────
  rm -rf "${worktree}"
  git -C "${repo_dir}" worktree add "${worktree}" "origin/${base_branch}" \
    >>"${log_file}" 2>&1

  # ── select agent ─────────────────────────────────────────────────────────
  local agent_cmd
  case "${mtype}" in
    bugfix-loop)  agent_cmd="codex" ;;
    *)            agent_cmd="claude" ;;  # evaluation-loop, feature-loop, etc.
  esac

  # ── build prompt ─────────────────────────────────────────────────────────
  local prompt
  prompt="$(printf 'Mission %s\nType: %s\nObjective: %s\n\nAllowed paths: %s\nForbidden paths: %s\n\nSuccess criteria:\n%s' \
    "${code}" "${mtype}" "${objective}" \
    "$(printf '%s' "${contract_json}" | jq -r '.allowed_paths[]? // "all" ' | tr '\n' ' ')" \
    "$(printf '%s' "${contract_json}" | jq -r '.forbidden_paths[]? // "none"' | tr '\n' ' ')" \
    "$(printf '%s' "${contract_json}" | jq -r '.success_criteria[]? // ""' | tr '\n' '\n  ')" \
  )"

  # ── execute with timeout ─────────────────────────────────────────────────
  local exit_code=0 stop_reason="success_criteria_met"
  log "Running ${agent_cmd} (timeout=${timeout_s}s)"
  if ! (
    cd "${worktree}"
    timeout "${timeout_s}" "${agent_cmd}" --print "${prompt}" \
      >>"${log_file}" 2>&1
  ); then
    exit_code=$?
    if [[ ${exit_code} -eq 124 ]]; then
      stop_reason="time_limit_reached"
    else
      stop_reason="persistent_failure"
    fi
  fi

  # ── collect artifacts (ledger) ───────────────────────────────────────────
  echo '{}' | maestro_post "/missions/${code}/artifacts:collect"

  # ── report terminal state ────────────────────────────────────────────────
  if [[ ${exit_code} -eq 0 ]]; then
    report_delivered "${code}"
  else
    report_stop "${code}" "${stop_reason}"
  fi

  # ── cleanup worktree ─────────────────────────────────────────────────────
  git -C "${repo_dir}" worktree remove "${worktree}" --force \
    >>"${log_file}" 2>&1 || true
}

# ── main poll loop ───────────────────────────────────────────────────────────

log "rbx-agent-runner starting (runner=${RUNNER_ID}, maestro=${MAESTRO_URL})"

while true; do
  raw=$(poll_next 2>/dev/null || printf '\n000')
  http_code=$(printf '%s' "${raw}" | tail -1)
  body=$(printf '%s' "${raw}" | head -n -1)

  case "${http_code}" in
    204)
      sleep "${POLL_INTERVAL_S}"
      ;;
    200)
      mission_code=$(printf '%s' "${body}" | jq -r '.mission_code')
      contract=$(printf '%s' "${body}" | jq -c '.contract')

      log "Claimed ${mission_code}"

      # Heartbeat in background
      heartbeat_loop "${mission_code}" &
      hb_pid=$!

      execute_mission "${mission_code}" "${contract}" || {
        log "ERROR execute_mission failed for ${mission_code}"
        report_stop "${mission_code}" "persistent_failure"
      }

      kill "${hb_pid}" 2>/dev/null || true
      wait "${hb_pid}" 2>/dev/null || true
      ;;
    000)
      log "WARN network error polling /leases/next, retry in ${POLL_INTERVAL_S}s"
      sleep "${POLL_INTERVAL_S}"
      ;;
    *)
      log "WARN unexpected HTTP ${http_code} from /leases/next"
      sleep "${POLL_INTERVAL_S}"
      ;;
  esac
done
