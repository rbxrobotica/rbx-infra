#!/usr/bin/env bash
# rbx-agent-watchdog — Corbetti runner health monitor (companion to rbx-agent-runner)
#
# Runs every 90s (OnUnitActiveSec) via rbx-agent-watchdog.timer. Detects:
#   auth-fail      — a claude/codex executor exited non-zero with an auth-error
#                    signature in the mission log (OAuth session expired, etc.) and
#                    the mission never reached DELIVERED.
#   verify-streak  — WATCHDOG_VERIFY_STREAK (default 3) consecutive verify=failed.
#   stall          — most-recently-claimed mission log untouched for
#                    WATCHDOG_STALL_MIN (default 20) minutes with no terminal state.
#   budget         — rolling 24h runner tokens > WATCHDOG_24H_TOKEN_CAP
#                    (blocks new claims, cleanly stops the active mission, then
#                    stops the runner + alerts; under RUNNER-DIRECT-001 provider
#                    spend is not Thalamus-mediated, so the watchdog enforces the
#                    cap for Claude/GLM/Codex; the current Kimi executor does not
#                    emit structured usage).
#   runner-down    — rbx-agent-runner.service not active.
#
# Escalation is §2-compliant: opens ONE GitHub issue assigned to the operator on
# WATCHDOG_MONITORING_REPO (deterministic code; GitHub-mention is the CEO-chosen
# notification channel). The watchdog performs NO publish, push, apply, or external
# call beyond that single `gh issue create`; the only other mutating actions are
# the local budget-stop marker and `systemctl --user stop rbx-agent-runner`
# after any active mission has reached a terminal state.
#
# Auth for `gh`: uses the operator's own `gh` session on Corbetti (NOT the runner's
# scoped GITHUB_PAT — that PAT has no issues:write on the monitoring repo).
#
# Sources: ~/rbx/runner/.env (same as the runner)
# State:   ~/.rbx/watchdog/fired.hash (dedupe, 6h TTL)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/.env"

LOG_DIR="${HOME}/rbx/logs"
STATE_DIR="${HOME}/.rbx/watchdog"
ACTIVE_MISSION_FILE="${STATE_DIR}/active-mission"
BUDGET_STOP_FILE="${STATE_DIR}/budget-stop"
mkdir -p "${STATE_DIR}" "${LOG_DIR}"

# devbox PATH (gh, curl, jq, systemctl, journalctl, stat)
export PATH="${HOME}/rbx/bin:${HOME}/.local/bin:${HOME}/rbx/.devbox/nix/profile/default/bin:${HOME}/rbx/.devbox/npm-global/bin:${PATH}"

STALL_MIN="${WATCHDOG_STALL_MIN:-20}"
VERIFY_STREAK="${WATCHDOG_VERIFY_STREAK:-3}"
MONITORING_REPO="${WATCHDOG_MONITORING_REPO:-}"
TOKEN_CAP="${WATCHDOG_24H_TOKEN_CAP:-}"

ts()  { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
log() { printf '[%s] [watchdog] %s\n' "$(ts)" "$*" >&2; }

# ── dedupe: each (kind, key) fires at most once per 6h ────────────────────────
fired_file="${STATE_DIR}/fired.hash"
touch "${fired_file}"
now_epoch=$(date +%s)
: > "${STATE_DIR}/.fired.new"
while IFS='|' read -r kind key exp; do
  [[ -z "${kind}" ]] && continue
  (( exp > now_epoch )) && printf '%s|%s|%s\n' "${kind}" "${key}" "${exp}" >> "${STATE_DIR}/.fired.new"
done < "${fired_file}"
mv "${STATE_DIR}/.fired.new" "${fired_file}"

already_fired() { grep -q "^${1}|${2}|" "${fired_file}" 2>/dev/null; }
mark_fired()    { printf '%s|%s|%s\n' "$1" "$2" $(( now_epoch + 6*3600 )) >> "${fired_file}"; }

alert() {
  # $1 kind  $2 key  $3 title  $4 body
  local kind="$1" key="$2" title="$3" body="$4"
  if already_fired "${kind}" "${key}"; then
    log "suppressed (dedupe) ${kind} ${key}"
    return 0
  fi
  if [[ -z "${MONITORING_REPO}" ]]; then
    log "ALERT (no WATCHDOG_MONITORING_REPO): ${title} :: ${body}"
    mark_fired "${kind}" "${key}"; return 0
  fi
  local url
  url=$(gh issue create --repo "${MONITORING_REPO}" --assignee ldamasio \
        --title "${title}" --body "${body}" 2>/dev/null || true)
  if [[ -n "${url}" ]]; then
    log "ALERT ${kind} ${key} -> ${url}"
  else
    log "ALERT ${kind} ${key} (gh issue create FAILED — check operator gh auth)"
  fi
  mark_fired "${kind}" "${key}"
}

# Clear a stale active marker left by an unclean runner exit. A live marker
# contains the runner shell PID plus mission code, separated by `|`.
runner_busy=false
active_code=""
if [[ -f "${ACTIVE_MISSION_FILE}" ]]; then
  IFS='|' read -r active_pid active_code < "${ACTIVE_MISSION_FILE}" || true
  if [[ "${active_pid:-}" =~ ^[0-9]+$ ]] && kill -0 "${active_pid}" 2>/dev/null; then
    runner_busy=true
  else
    rm -f "${ACTIVE_MISSION_FILE}"
    active_code=""
  fi
fi

# ── 1. runner-down ───────────────────────────────────────────────────────────
if ! systemctl --user is-active rbx-agent-runner >/dev/null 2>&1; then
  if [[ -f "${BUDGET_STOP_FILE}" ]]; then
    log "runner intentionally down: budget-stop marker present"
  else
    alert "runner-down" "runner" \
      "rbx-agent-runner is NOT active" \
      "rbx-agent-runner.service is not active on Corbetti at $(ts). Unattended missions are not being processed. Investigate \`systemctl --user status rbx-agent-runner\` and \`journalctl --user -u rbx-agent-runner -n 50\`."
  fi
fi

# ── 2. auth-fail (claude/codex OAuth expiry under RUNNER-DIRECT-001) ─────────
# HTTP status codes require HTTP/status/auth context; bare numeric 401/403
# values occur normally in streamed JSON (for example estimated_tokens: 401).
AUTH_RE='([Hh][Tt][Tt][Pp][^[:digit:]]*(401|403)|(status|status_code|status code)[^[:digit:]]*(401|403)|(401|403)[[:space:]:_-]+([Uu]nauthorized|[Ff]orbidden)|[Uu]nauthorized|invalid[_ -]?(api[_ -]?key|key|token)|[Oo][Aa]uth[^[:alnum:]]*(error|expired|failed|invalid|required)|login required|session expired|not authenticated|not logged in|account[ _-]*(disabled|suspended)|expired[_ ]*token)'
while IFS= read -r -d '' logfile; do
  code=$(basename "${logfile}" .log)
  if grep -Eiq "${AUTH_RE}" "${logfile}" 2>/dev/null && ! grep -q 'DELIVERED' "${logfile}" 2>/dev/null; then
    tail_snip=$(tail -n 8 "${logfile}" 2>/dev/null | sed 's/`//g' | tr '\n' ' ' | cut -c1-600)
    alert "auth-fail" "${code}" \
      "runner auth-fail on ${code}" \
      "Mission ${code} log shows an authentication-error signature with no DELIVERED — likely OAuth session expiry under RUNNER-DIRECT-001. Re-login on Corbetti (\`claude /login\` / \`codex login\`) and restart. Log tail: ${tail_snip}"
  fi
done < <(find "${LOG_DIR}" -maxdepth 1 -name '*.log' -mmin -30 -print0 2>/dev/null)

# ── 3. verify-streak ─────────────────────────────────────────────────────────
verify_fails=$(journalctl --user -u rbx-agent-runner --no-pager -n 200 2>/dev/null \
  | grep -E 'verify_status=failed|verify=failed' | tail -n "${VERIFY_STREAK}" || true)
fail_count=$(printf '%s\n' "${verify_fails}" | grep -c . 2>/dev/null || true)
if (( fail_count >= VERIFY_STREAK )); then
  alert "verify-streak" "global" \
    "runner verify-fail streak (${fail_count})" \
    "${fail_count} recent verify=failed outcomes (threshold ${VERIFY_STREAK}). Missions may be mis-targeted or verify_command may be wrong. Check eden /missions and ~/rbx/logs."
fi

# ── 4. stall ─────────────────────────────────────────────────────────────────
last_code=$(journalctl --user -u rbx-agent-runner --no-pager -n 200 2>/dev/null \
  | grep -oE 'Claimed [A-Za-z0-9_-]+' | tail -1 | awk '{print $2}' || true)
if [[ -n "${last_code}" ]]; then
  logfile="${LOG_DIR}/${last_code}.log"
  if [[ -f "${logfile}" ]]; then
    stale_epoch=$(( now_epoch - STALL_MIN*60 ))
    mtime=$(stat -c %Y "${logfile}" 2>/dev/null || echo 0)
    if (( mtime > 0 && mtime < stale_epoch )) && ! grep -q 'DELIVERED\|STOP' "${logfile}" 2>/dev/null; then
      alert "stall" "${last_code}" \
        "runner stall on ${last_code}" \
        "Mission ${last_code} log untouched for >${STALL_MIN} min with no terminal state — executor may be hung. Inspect \`tail ~/rbx/logs/${last_code}.log\`."
    fi
  fi
fi

# ── 5. budget (rolling 24h observed result tokens) ───────────────────────────
if [[ "${TOKEN_CAP}" =~ ^[0-9]+$ ]]; then
  total=0
  while IFS= read -r -d '' logfile; do
    while IFS= read -r line; do
      [[ -z "${line}" ]] && continue
      event_type=$(printf '%s' "${line}" | jq -r '.type // empty' 2>/dev/null || true)
      if [[ "${event_type}" == "result" ]]; then
        # Claude/GLM result events may report cache tokens separately.
        in=$(printf '%s' "${line}" | jq -r '
          [.usage.input_tokens, .usage.cache_creation_input_tokens, .usage.cache_read_input_tokens]
          | map(select(type == "number"))
          | if length == 0 then empty else add end
        ' 2>/dev/null || true)
      else
        # Codex turn.completed input_tokens includes cached_input_tokens.
        in=$(printf '%s' "${line}" | jq -r '.usage.input_tokens // empty' 2>/dev/null || true)
      fi
      out=$(printf '%s' "${line}" | jq -r '.usage.output_tokens // empty' 2>/dev/null || true)
      [[ "${in}"  =~ ^[0-9]+$ ]] && total=$(( total + in ))
      [[ "${out}" =~ ^[0-9]+$ ]] && total=$(( total + out ))
    done < <(grep -E '"type"[[:space:]]*:[[:space:]]*"(result|turn\.completed)"' \
      "${logfile}" 2>/dev/null || true)
  done < <(find "${LOG_DIR}" -maxdepth 1 -name '*.log' -mtime -1 -print0 2>/dev/null)
  if (( total > TOKEN_CAP )); then
    touch "${BUDGET_STOP_FILE}"
    alert "budget" "24h" \
      "runner 24h token budget exceeded (${total} > ${TOKEN_CAP})" \
      "Rolling-24h runner tokens (${total}) exceeded WATCHDOG_24H_TOKEN_CAP (${TOKEN_CAP}). New claims are blocked. Any active mission will stop as cost_limit_reached before persistence; the runner then stops fail-safe. Resume only after operator review."
    if [[ "${runner_busy}" == "true" ]]; then
      log "budget stop deferred: active mission ${active_code:-unknown} must reach terminal state"
    else
      systemctl --user stop rbx-agent-runner 2>/dev/null || true
    fi
  fi
  log "budget: ${total}/${TOKEN_CAP} tokens (rolling 24h)"
fi

log "ok (runner-down/auth-fail/verify-streak/stall/budget checked)"
