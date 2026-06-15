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

# rbx/bin (glm/codex wrappers), rtk/lean-ctx, kimi-code cli, devbox tools
export PATH="${HOME}/rbx/bin:${HOME}/.local/bin:${HOME}/.kimi-code/bin:${HOME}/rbx/.devbox/nix/profile/default/bin:${HOME}/rbx/.devbox/npm-global/bin:${PATH}"

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
  local raw http_code
  raw=$(curl -sf -XPOST \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${AGENT_LOOP_RUNNER_KEY}" \
    -H "X-Runner-Id: ${RUNNER_ID}" \
    -d @- \
    -w '\n%{http_code}' \
    "${MAESTRO_URL}${path}" 2>/dev/null || printf '\n000')
  http_code=$(printf '%s' "${raw}" | tail -1)
  if [[ "${http_code}" != 2[0-9][0-9] ]]; then
    log "WARN maestro POST ${path} returned HTTP ${http_code}"
    return 1
  fi
  return 0
}

heartbeat_loop() {
  local code="$1"
  while true; do
    sleep "${HEARTBEAT_INTERVAL_S}"
    echo '{}' | maestro_post "/missions/${code}/lease/heartbeat" || true
  done
}

report_stop() {
  local code="$1" reason="$2"
  if printf '{"state":"stopped","stop_reason":"%s"}' "${reason}" \
    | maestro_post "/missions/${code}/lease/state"; then
    log "STOP ${code}: ${reason}"
  else
    log "WARN failed to report stop for ${code}: ${reason}"
  fi
}

report_delivered() {
  local code="$1" input_tok="${2:-}" output_tok="${3:-}"
  local payload
  if [[ -n "${input_tok}" && -n "${output_tok}" ]]; then
    payload=$(printf '{"state":"delivered","input_tokens":%s,"output_tokens":%s}' \
      "${input_tok}" "${output_tok}")
  else
    payload='{"state":"delivered"}'
  fi
  if printf '%s' "${payload}" | maestro_post "/missions/${code}/lease/state"; then
    log "DELIVERED ${code} (in=${input_tok:-?} out=${output_tok:-?})"
  else
    log "WARN failed to report delivered for ${code}"
  fi
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
  git -C "${repo_dir}" worktree add "${worktree}" "${base_branch}" \
    >>"${log_file}" 2>&1

  # ── select executor (Phase 5: executor field overrides mtype heuristic) ──
  local executor
  executor=$(printf '%s' "${contract_json}" | jq -r '.executor // "claude-haiku"')

  local agent_cmd agent_model captures_tokens=false
  case "${executor}" in
    claude-haiku)
      agent_cmd="claude"
      agent_model="${CLAUDE_MODEL:-claude-haiku-4-5-20251001}"
      captures_tokens=true
      ;;
    claude-sonnet)
      agent_cmd="claude"
      agent_model="${CLAUDE_SONNET_MODEL:-claude-sonnet-4-5-20251001}"
      captures_tokens=true
      ;;
    glm)
      agent_cmd="glm"
      agent_model="glm-4.7"
      captures_tokens=true
      ;;
    kimi)
      agent_cmd="kimi"
      agent_model="k2.7"
      captures_tokens=false
      ;;
    codex)
      agent_cmd="codex"
      agent_model="o4-mini"
      captures_tokens=false
      ;;
    *)
      log "WARN unknown executor '${executor}', falling back to claude-haiku"
      agent_cmd="claude"
      agent_model="${CLAUDE_MODEL:-claude-haiku-4-5-20251001}"
      captures_tokens=true
      ;;
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
  log "Running ${agent_cmd} executor=${executor} model=${agent_model} (timeout=${timeout_s}s)"
  if ! (
    cd "${worktree}"
    case "${agent_cmd}" in
      claude|glm)
        # stream-json captures token usage in the final result line
        timeout "${timeout_s}" "${agent_cmd}" --print \
          --output-format stream-json \
          --model "${agent_model}" "${prompt}" \
          >>"${log_file}" 2>&1
        ;;
      kimi)
        timeout "${timeout_s}" kimi -p "${prompt}" \
          >>"${log_file}" 2>&1
        ;;
      codex)
        timeout "${timeout_s}" codex --approval-mode full-auto --quiet "${prompt}" \
          >>"${log_file}" 2>&1
        ;;
      *)
        timeout "${timeout_s}" "${agent_cmd}" --print "${prompt}" \
          >>"${log_file}" 2>&1
        ;;
    esac
  ); then
    exit_code=$?
    if [[ ${exit_code} -eq 124 ]]; then
      stop_reason="time_limit_reached"
    else
      stop_reason="persistent_failure"
    fi
  fi

  # ── extract token usage from stream-json result line ─────────────────────
  local input_tok="" output_tok=""
  if [[ "${captures_tokens}" == "true" && ${exit_code} -eq 0 ]]; then
    local result_line
    result_line=$(grep '"type":"result"' "${log_file}" | tail -1 || true)
    if [[ -n "${result_line}" ]]; then
      input_tok=$(printf '%s' "${result_line}" | jq -r '.usage.input_tokens // empty' 2>/dev/null || true)
      output_tok=$(printf '%s' "${result_line}" | jq -r '.usage.output_tokens // empty' 2>/dev/null || true)
    fi
  fi

  # ── collect artifacts (ledger) ───────────────────────────────────────────
  echo '{}' | maestro_post "/missions/${code}/artifacts:collect"

  # ── report terminal state ────────────────────────────────────────────────
  if [[ ${exit_code} -eq 0 ]]; then
    report_delivered "${code}" "${input_tok}" "${output_tok}"
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
