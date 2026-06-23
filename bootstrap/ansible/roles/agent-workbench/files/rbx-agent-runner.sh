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
  local code="$1" reason="$2" vstatus="${3:-}" vexit="${4:-}"
  local verify_json=""
  [[ -n "${vstatus}" ]] && verify_json+=",\"verify_status\":\"${vstatus}\""
  [[ -n "${vexit}" ]] && verify_json+=",\"verify_exit_code\":${vexit}"
  if printf '{"state":"stopped","stop_reason":"%s"%s}' "${reason}" "${verify_json}" \
    | maestro_post "/missions/${code}/lease/state"; then
    log "STOP ${code}: ${reason} (verify=${vstatus:-n/a})"
  else
    log "WARN failed to report stop for ${code}: ${reason}"
  fi
}

report_delivered() {
  local code="$1" input_tok="${2:-}" output_tok="${3:-}" vstatus="${4:-}" vexit="${5:-}"
  local payload verify_json=""
  [[ -n "${vstatus}" ]] && verify_json+=",\"verify_status\":\"${vstatus}\""
  [[ -n "${vexit}" ]] && verify_json+=",\"verify_exit_code\":${vexit}"
  if [[ -n "${input_tok}" && -n "${output_tok}" ]]; then
    payload=$(printf '{"state":"delivered","input_tokens":%s,"output_tokens":%s%s}' \
      "${input_tok}" "${output_tok}" "${verify_json}")
  else
    payload=$(printf '{"state":"delivered"%s}' "${verify_json}")
  fi
  if printf '%s' "${payload}" | maestro_post "/missions/${code}/lease/state"; then
    log "DELIVERED ${code} (in=${input_tok:-?} out=${output_tok:-?} verify=${vstatus:-n/a})"
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
      agent_model="glm-5.2"
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

  # ── build prompt (ADR-0019: done_criteria + verify_command) ───────────────
  # done_criteria is canonical post-C1; // success_criteria keeps pre-C1
  # contracts readable until they age out of the registry.
  local prompt done_bullets verify_cmd_for_prompt
  done_bullets=$(printf '%s' "${contract_json}" \
    | jq -r '(.done_criteria // .success_criteria // [])[]?' | sed 's/^/  - /')
  verify_cmd_for_prompt=$(printf '%s' "${contract_json}" | jq -r '.verify_command // ""')
  prompt="Mission ${code}
Type: ${mtype}
Objective: ${objective}

Allowed paths: $(printf '%s' "${contract_json}" | jq -r '.allowed_paths[]? // "all"' | tr '\n' ' ')
Forbidden paths: $(printf '%s' "${contract_json}" | jq -r '.forbidden_paths[]? // "none"' | tr '\n' ' ')

Done criteria (machine-checkable success target — your changes MUST satisfy these):
${done_bullets}
"
  if [[ -n "${verify_cmd_for_prompt}" ]]; then
    prompt+="
Verify command (the runner executes this to prove done; it MUST exit 0):
  ${verify_cmd_for_prompt}
"
  fi

  # ── execute with timeout ─────────────────────────────────────────────────
  local exit_code=0 stop_reason="success_criteria_met"
  log "Running ${agent_cmd} executor=${executor} model=${agent_model} (timeout=${timeout_s}s)"
  # NOTE: `exit_code=$?` must be captured via `|| exit_code=$?`, never inside
  # `if ! (cmd); then exit_code=$?; fi` — the `!` negation means `$?` inside
  # the then-branch is the if-condition's own status (always 0), not cmd's.
  # That bug previously made every mission report DELIVERED regardless of
  # whether the agent actually succeeded.
  (
    cd "${worktree}"
    case "${agent_cmd}" in
      claude|glm)
        # stream-json captures token usage in the final result line.
        # --verbose is required by current claude CLI when combining
        # --print with --output-format stream-json.
        timeout "${timeout_s}" "${agent_cmd}" --print \
          --output-format stream-json --verbose \
          --model "${agent_model}" "${prompt}" \
          >>"${log_file}" 2>&1
        ;;
      kimi)
        timeout "${timeout_s}" kimi -p "${prompt}" \
          >>"${log_file}" 2>&1
        ;;
      codex)
        # `codex exec` is the non-interactive subcommand; bare `codex` always
        # drops into the interactive TUI regardless of flags, which is what
        # caused it to block on an approval prompt previously.
        # --dangerously-bypass-approvals-and-sandbox: codex's bundled
        # bubblewrap sandbox fallback can't actually grant writes on this
        # VPS (no native bubblewrap installed), so -s workspace-write
        # silently blocks every file write. This flag is intended for
        # environments that are already externally sandboxed, which
        # applies here (per-mission git worktree, scoped GitHub PAT, no
        # prod secrets) — the other 4 executors run with no internal
        # sandbox at all, so this brings codex to parity, not above it.
        timeout "${timeout_s}" codex exec --dangerously-bypass-approvals-and-sandbox \
          --skip-git-repo-check "${prompt}" \
          >>"${log_file}" 2>&1
        ;;
      *)
        timeout "${timeout_s}" "${agent_cmd}" --print "${prompt}" \
          >>"${log_file}" 2>&1
        ;;
    esac
  ) || exit_code=$?
  if [[ ${exit_code} -ne 0 ]]; then
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

  # ── verify gate (ADR-0019): done is provable iff verify_command exits 0 ──
  # Runs only after the executor itself succeeded and only when the contract
  # declares verify_command (code-producing loops). verify_status stays
  # "not_run" otherwise, preserving the legacy executor-exit-code gate for
  # read-only loops and pre-C1 contracts. On failure the mission STOPS
  # (persistent_failure) for plan revision — no delivered, no PR.
  local verify_status="not_run" verify_exit_code=0 verify_cmd
  verify_cmd=$(printf '%s' "${contract_json}" | jq -r '.verify_command // empty')
  if [[ ${exit_code} -eq 0 && -n "${verify_cmd}" ]]; then
    log "VERIFY ${code}: ${verify_cmd}"
    {
      echo "--- verify_command: ${verify_cmd} --- $(ts)"
    } >> "${log_file}"
    ( cd "${worktree}" && timeout "${timeout_s}" bash -c "${verify_cmd}" ) \
      >>"${log_file}" 2>&1 || verify_exit_code=$?
    if [[ ${verify_exit_code} -eq 0 ]]; then
      verify_status="passed"
    else
      verify_status="failed"
      stop_reason="persistent_failure"
    fi
  fi

  # ── collect artifacts (ledger) ───────────────────────────────────────────
  echo '{}' | maestro_post "/missions/${code}/artifacts:collect"

  # ── report terminal state (ADR-0019: delivered only when verify passes) ──
  local report_vexit=""
  if [[ "${verify_status}" != "not_run" ]]; then
    report_vexit="${verify_exit_code}"
  fi
  if [[ ${exit_code} -eq 0 && "${verify_status}" != "failed" ]]; then
    report_delivered "${code}" "${input_tok}" "${output_tok}" "${verify_status}" "${report_vexit}"
  else
    report_stop "${code}" "${stop_reason}" "${verify_status}" "${report_vexit}"
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
