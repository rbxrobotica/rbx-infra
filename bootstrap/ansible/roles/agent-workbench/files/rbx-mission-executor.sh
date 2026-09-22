#!/usr/bin/env bash
# Execute one claimed MissionSpec through the repository-bound pipeline:
# worktree -> provider adapter -> path/diff policy -> verify -> branch/PR ->
# atomic Maestro result. This process owns no queue state.

set -euo pipefail

[[ $# -eq 5 ]] || { echo "usage: $0 MISSION_CODE CONTRACT_FILE LEASE_ID CLAIM_TOKEN CLAIM_GENERATION" >&2; exit 64; }
code="$1"
contract_file="$2"
lease_id="$3"
claim_token="$4"
claim_generation="$5"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADAPTER="${SCRIPT_DIR}/rbx-executor-adapter.sh"
POLICY_CHECK="${SCRIPT_DIR}/rbx-mission-policy.py"
LOG_DIR="${HOME}/rbx/logs"
WORKTREE_DIR="${HOME}/rbx/worktrees"
REPOS_DIR="${HOME}/rbx/repos"
MANIFEST_ROOT="${HOME}/rbx/manifests"
BUDGET_STOP_FILE="${HOME}/.rbx/watchdog/budget-stop"
manifest_dir="${MANIFEST_ROOT}/${code}"
log_file="${LOG_DIR}/${code}.log"
worktree="${WORKTREE_DIR}/${code}"
adapter_meta="${manifest_dir}/adapter.json"
adapter_result="${manifest_dir}/adapter-result.json"
adapter_raw="${manifest_dir}/adapter-output.log"
policy_result="${manifest_dir}/path-policy.json"
verify_result="${manifest_dir}/verify.json"
result_file="${manifest_dir}/result.json"
response_file="${manifest_dir}/maestro-response.json"
prompt_file="${manifest_dir}/prompt.txt"

mkdir -p "${LOG_DIR}" "${MANIFEST_ROOT}" "${manifest_dir}"
printf 'null\n' >"${policy_result}"
printf 'null\n' >"${verify_result}"

ts()  { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
log() { echo "[$(ts)] [${RUNNER_ID}] $*" | tee -a "${log_file}"; }

started_at="$(ts)"
started_epoch="$(date +%s)"
base_commit=""
repo_dir=""
finalized=false

cleanup() {
  if [[ "${finalized}" == "true" && -n "${repo_dir}" && -d "${repo_dir}" ]]; then
    git -C "${repo_dir}" worktree remove "${worktree}" --force >>"${log_file}" 2>&1 || true
  elif [[ "${finalized}" != "true" && -d "${worktree}" ]]; then
    log "PRESERVE ${code}: result not acknowledged; worktree retained at ${worktree}"
  fi
}
trap cleanup EXIT

legacy_post() {
  local path="$1" payload="$2" http_code curl_rc
  set +e
  http_code="$(curl -sS -o /dev/null -w '%{http_code}' -XPOST \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${AGENT_LOOP_RUNNER_KEY}" \
    -H "X-Runner-Id: ${RUNNER_ID}" \
    --data-binary "${payload}" "${MAESTRO_URL}${path}")"
  curl_rc=$?
  set -e
  [[ ${curl_rc} -eq 0 && "${http_code}" == 2[0-9][0-9] ]]
}

submit_result_file() {
  local outcome="$1" reason="${2:-}" http_code curl_rc attempt
  for attempt in 1 2 3; do
    set +e
    http_code="$(curl -sS -o "${response_file}" -w '%{http_code}' -XPOST \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer ${AGENT_LOOP_RUNNER_KEY}" \
      -H "X-Runner-Id: ${RUNNER_ID}" \
      --data-binary @"${result_file}" \
      "${MAESTRO_URL}/missions/${code}/result")"
    curl_rc=$?
    set -e
    if [[ ${curl_rc} -eq 0 && "${http_code}" == 2[0-9][0-9] ]]; then
      log "RESULT ${code}: ${outcome} accepted by Maestro (HTTP ${http_code})"
      return 0
    fi
    if [[ ${curl_rc} -eq 0 && ("${http_code}" == "404" || "${http_code}" == "405") ]]; then
      log "RESULT ${code}: Maestro v1 endpoint unavailable; using legacy terminal transition"
      legacy_post "/missions/${code}/artifacts:collect" '{}' || true
      if [[ "${outcome}" == "delivered" ]]; then
        local payload
        payload="$(jq -c '{state:"delivered",input_tokens:.execution.input_tokens,output_tokens:.execution.output_tokens,verify_status:.delivery.verify.status,verify_exit_code:.delivery.verify.exit_code} | with_entries(select(.value != null))' "${result_file}")"
        legacy_post "/missions/${code}/lease/state" "${payload}"
      else
        local payload
        payload="$(jq -c --arg reason "${reason}" '{state:"stopped",stop_reason:$reason,verify_status:.failure.verify.status,verify_exit_code:.failure.verify.exit_code} | with_entries(select(.value != null))' "${result_file}")"
        legacy_post "/missions/${code}/lease/state" "${payload}"
      fi
      return $?
    fi
    log "WARN result POST attempt ${attempt}/3 failed (curl=${curl_rc}, HTTP=${http_code:-000})"
    [[ ${attempt} -eq 3 ]] || sleep $(( attempt * 2 ))
  done
  return 1
}

execution_json() {
  local finished_at="$1" source="${adapter_meta}"
  [[ -s "${adapter_result}" ]] && source="${adapter_result}"
  jq -n --slurpfile meta "${source}" \
    --arg code "${code}" --arg lease_id "${lease_id}" --arg claim_token "${claim_token}" \
    --arg runner_id "${RUNNER_ID}" --arg repo "${repo}" --arg base_branch "${base_branch}" \
    --arg base_commit "${base_commit}" --arg attempt "${claim_generation}" --arg started_at "${started_at}" --arg finished_at "${finished_at}" \
    '{schema_version:"1",mission_code:$code,lease_id:$lease_id,claim_token:$claim_token,
      runner_id:$runner_id,executor:$meta[0].executor,adapter:$meta[0].adapter,
      provider:$meta[0].provider,model:$meta[0].model,repository:$repo,
      base_branch:$base_branch,base_commit:$base_commit,attempt:($attempt | tonumber),
      started_at:$started_at,finished_at:$finished_at,
      input_tokens:($meta[0].input_tokens // null),output_tokens:($meta[0].output_tokens // null)}'
}

submit_failure() {
  local phase="$1" message="$2" reason="$3" finished execution
  finished="$(ts)"
  execution="$(execution_json "${finished}")"
  jq -n --arg lease_id "${lease_id}" --arg claim_token "${claim_token}" \
    --argjson execution "${execution}" --arg phase "${phase}" --arg message "${message}" \
    --arg reason "${reason}" --slurpfile policy "${policy_result}" --slurpfile verify "${verify_result}" \
    '{schema_version:"1",lease_id:$lease_id,claim_token:$claim_token,execution:$execution,
      failure:{schema_version:"1",outcome:"stopped",stop_reason:$reason,phase:$phase,message:$message,
        changed_files:($policy[0].changed_files // []),diff_files:($policy[0].diff_files // 0),
        diff_lines:($policy[0].diff_lines // 0),
        path_policy:(if $policy[0] == null then null else {status:$policy[0].status,violations:$policy[0].violations} end),
        verify:$verify[0]}}' >"${result_file}"
  jq '.execution' "${result_file}" >"${manifest_dir}/execution.json"
  jq '.failure' "${result_file}" >"${manifest_dir}/failure.json"
  if submit_result_file stopped "${reason}"; then
    finalized=true
    log "STOP ${code}: ${reason} phase=${phase}"
    return 0
  fi
  log "ERROR ${code}: failure manifest was not acknowledged"
  return 1
}

submit_delivery() {
  local branch="$1" head_commit="$2" pr_url="$3" finished execution
  finished="$(ts)"
  execution="$(execution_json "${finished}")"
  jq -n --arg lease_id "${lease_id}" --arg claim_token "${claim_token}" \
    --argjson execution "${execution}" --arg branch "${branch}" --arg head_commit "${head_commit}" \
    --arg pr_url "${pr_url}" --slurpfile policy "${policy_result}" --slurpfile verify "${verify_result}" \
    '{schema_version:"1",lease_id:$lease_id,claim_token:$claim_token,execution:$execution,
      delivery:{schema_version:"1",outcome:"delivered",branch:$branch,head_commit:$head_commit,
        pull_request_url:$pr_url,changed_files:$policy[0].changed_files,diff_files:$policy[0].diff_files,
        diff_lines:$policy[0].diff_lines,path_policy:{status:$policy[0].status,violations:$policy[0].violations},
        verify:$verify[0]}}' >"${result_file}"
  jq '.execution' "${result_file}" >"${manifest_dir}/execution.json"
  jq '.delivery' "${result_file}" >"${manifest_dir}/delivery.json"
  if submit_result_file delivered; then
    finalized=true
    log "DELIVERED ${code}: ${pr_url}"
    return 0
  fi
  log "ERROR ${code}: delivery manifest was not acknowledged"
  return 1
}

duration_seconds() {
  python3 - "$1" <<'PY'
import re, sys
m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", sys.argv[1])
if not m or not any(m.groups()):
    raise SystemExit(2)
d, h, minute, sec = (int(v or 0) for v in m.groups())
print(d * 86400 + h * 3600 + minute * 60 + sec)
PY
}

remaining_seconds() {
  local elapsed
  elapsed=$(( $(date +%s) - started_epoch ))
  if (( timeout_s > elapsed )); then echo $(( timeout_s - elapsed )); else echo 0; fi
}

repo="$(jq -er '.repo' "${contract_file}")"
base_branch="$(jq -er '.base_branch' "${contract_file}")"
source_commit="$(jq -r '.source_commit // empty' "${contract_file}")"
mtype="$(jq -er '.type' "${contract_file}")"
objective="$(jq -er '.objective' "${contract_file}")"
executor="$(jq -r '.executor // "claude-haiku"' "${contract_file}")"
max_runtime="$(jq -er '.max_runtime' "${contract_file}")"
max_cost="$(jq -er '.max_cost' "${contract_file}")"
timeout_s="$(duration_seconds "${max_runtime}")" || { log "invalid max_runtime: ${max_runtime}"; exit 65; }

if [[ ! "${code}" =~ ^mission-[0-9]{4}-[0-9]{5}$ || ! "${repo}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] ||
   ! git check-ref-format --branch "${base_branch}" >/dev/null 2>&1 ||
   [[ -n "${source_commit}" && ! "${source_commit}" =~ ^[0-9a-f]{40}$ ]]; then
  log "refusing invalid mission/repository/base branch identity"
  exit 65
fi

if ! "${ADAPTER}" describe "${executor}" >"${adapter_meta}"; then
  # A minimal manifest cannot truthfully name a provider for an unknown
  # executor, so refuse before touching a repository. Admission schema should
  # normally make this unreachable.
  log "unknown executor ${executor}; no repository mutation performed"
  exit 65
fi

log "START ${code} type=${mtype} repo=${repo} executor=${executor} timeout=${timeout_s}s"
{
  echo "=== mission ${code} === ${started_at}"
  printf 'type: %s\nrepo: %s\nbranch: %s\nexecutor: %s\nobjective: %s\n---\n' \
    "${mtype}" "${repo}" "${base_branch}" "${executor}" "${objective}"
} >>"${log_file}"

org="${repo%%/*}"
repo_name="${repo##*/}"
repo_dir="${REPOS_DIR}/${org}/${repo_name}.git"
mkdir -p "${REPOS_DIR}/${org}"

if [[ ! -d "${repo_dir}" ]]; then
  if ! git clone --bare "https://github.com/${repo}.git" "${repo_dir}" >>"${log_file}" 2>&1; then
    submit_failure repository_setup "bare clone failed" persistent_failure
    exit $?
  fi
fi
if ! git -C "${repo_dir}" fetch origin \
  "+refs/heads/${base_branch}:refs/remotes/origin/${base_branch}" >>"${log_file}" 2>&1; then
  submit_failure repository_setup "base branch fetch failed" persistent_failure
  exit $?
fi

checkout_ref="refs/remotes/origin/${base_branch}"
if [[ -n "${source_commit}" ]]; then
  if ! git -C "${repo_dir}" cat-file -e "${source_commit}^{commit}" 2>>"${log_file}" ||
     ! git -C "${repo_dir}" merge-base --is-ancestor "${source_commit}" \
       "refs/remotes/origin/${base_branch}" >>"${log_file}" 2>&1; then
    submit_failure repository_setup "source_commit is unavailable or outside the admitted base branch" persistent_failure
    exit $?
  fi
  checkout_ref="${source_commit}"
fi

git -C "${repo_dir}" worktree prune >>"${log_file}" 2>&1 || true
if [[ -d "${worktree}" ]]; then
  git -C "${repo_dir}" worktree remove "${worktree}" --force >>"${log_file}" 2>&1 || true
fi
if [[ -e "${worktree}" ]]; then
  submit_failure repository_setup "worktree path is occupied after prune" persistent_failure
  exit $?
fi
if ! git -C "${repo_dir}" worktree add --detach "${worktree}" \
  "${checkout_ref}" >>"${log_file}" 2>&1; then
  submit_failure repository_setup "detached worktree creation failed" persistent_failure
  exit $?
fi
if ! git -C "${worktree}" config user.name "${RUNNER_GIT_AUTHOR_NAME}" ||
   ! git -C "${worktree}" config user.email "${RUNNER_GIT_AUTHOR_EMAIL}"; then
  submit_failure repository_setup "git author configuration failed" persistent_failure
  exit $?
fi
base_commit="$(git -C "${worktree}" rev-parse HEAD)"
if [[ -n "${source_commit}" && "${base_commit}" != "${source_commit}" ]]; then
  submit_failure repository_setup "worktree HEAD does not match source_commit" persistent_failure
  exit $?
fi

done_bullets="$(jq -r '(.done_criteria // .success_criteria // [])[]? | "  - \(.)"' "${contract_file}")"
allowed_paths="$(jq -r '.allowed_paths[]?' "${contract_file}" | tr '\n' ' ')"
forbidden_paths="$(jq -r '.forbidden_paths[]?' "${contract_file}" | tr '\n' ' ')"
verify_cmd="$(jq -r '.verify_command // ""' "${contract_file}")"
cat >"${prompt_file}" <<EOF
Mission ${code}
Type: ${mtype}
Objective: ${objective}

Allowed paths: ${allowed_paths}
Forbidden paths: ${forbidden_paths}

Done criteria:
${done_bullets}

The runner enforces path scope, diff bounds, and verification after execution.
Do not push, merge, deploy, or modify files outside the allowed paths.
EOF
if [[ -n "${verify_cmd}" ]]; then
  printf '\nVerify command (executed by the runner):\n  %s\n' "${verify_cmd}" >>"${prompt_file}"
fi

agent_exit=0
remaining="$(remaining_seconds)"
if (( remaining <= 0 )); then
  submit_failure executor "runtime budget expired before executor start" time_limit_reached
  exit $?
fi
log "EXECUTE ${code}: adapter=$(jq -r '.adapter' "${adapter_meta}") remaining=${remaining}s"
"${ADAPTER}" run "${executor}" "${worktree}" "${prompt_file}" "${log_file}" \
  "${remaining}" "${adapter_result}" "${adapter_raw}" || agent_exit=$?

if ! git -C "${worktree}" add -A >>"${log_file}" 2>&1; then
  submit_failure path_policy "git staging failed" persistent_failure
  exit $?
fi
policy_exit=0
"${POLICY_CHECK}" "${contract_file}" "${worktree}" "${policy_result}" >>"${log_file}" 2>&1 || policy_exit=$?
if [[ ${policy_exit} -ne 0 ]]; then
  if [[ ${policy_exit} -eq 3 ]]; then
    reason="$(jq -r '.stop_reason' "${policy_result}")"
    submit_failure path_policy "repository path or diff policy rejected the staged changes" "${reason}"
  else
    submit_failure path_policy "path policy evaluator failed closed" persistent_failure
  fi
  exit $?
fi

if [[ ${agent_exit} -ne 0 ]]; then
  reason="persistent_failure"
  [[ ${agent_exit} -eq 124 ]] && reason="time_limit_reached"
  submit_failure executor "provider adapter exited ${agent_exit}" "${reason}"
  exit $?
fi

input_tokens="$(jq -r '.input_tokens // empty' "${adapter_result}")"
output_tokens="$(jq -r '.output_tokens // empty' "${adapter_result}")"
if [[ "${max_cost}" =~ ^([0-9]+)[[:space:]]*tokens$ ]]; then
  token_cap="${BASH_REMATCH[1]}"
  if [[ "${input_tokens}" =~ ^[0-9]+$ && "${output_tokens}" =~ ^[0-9]+$ ]] &&
     (( input_tokens + output_tokens > token_cap )); then
    submit_failure budget "provider usage exceeded the admitted token bound" cost_limit_reached
    exit $?
  fi
fi
if [[ -f "${BUDGET_STOP_FILE}" ]]; then
  submit_failure budget "rolling budget stop requested before verification" cost_limit_reached
  exit $?
fi

verify_status="not_run"
verify_exit=0
if [[ -n "${verify_cmd}" ]]; then
  remaining="$(remaining_seconds)"
  if (( remaining <= 0 )); then
    submit_failure verify "runtime budget expired before verification" time_limit_reached
    exit $?
  fi
  verify_started="$(ts)"
  log "VERIFY ${code}: ${verify_cmd} (remaining=${remaining}s)"
  (cd "${worktree}" && timeout "${remaining}" bash -c "${verify_cmd}") >>"${log_file}" 2>&1 || verify_exit=$?
  verify_finished="$(ts)"
  if [[ ${verify_exit} -eq 0 ]]; then verify_status="passed"; else verify_status="failed"; fi
  jq -n --arg command "${verify_cmd}" --arg status "${verify_status}" --argjson exit_code "${verify_exit}" \
    --arg started_at "${verify_started}" --arg finished_at "${verify_finished}" \
    '{command:$command,status:$status,exit_code:$exit_code,started_at:$started_at,finished_at:$finished_at}' >"${verify_result}"
else
  jq -n '{command:"",status:"not_run"}' >"${verify_result}"
fi

# Verification may create or modify files. Re-stage and re-evaluate policy so
# generated artifacts cannot escape allowed_paths after the first gate.
if ! git -C "${worktree}" add -A >>"${log_file}" 2>&1; then
  submit_failure path_policy "post-verify git staging failed" persistent_failure
  exit $?
fi
policy_exit=0
"${POLICY_CHECK}" "${contract_file}" "${worktree}" "${policy_result}" >>"${log_file}" 2>&1 || policy_exit=$?
if [[ ${policy_exit} -ne 0 ]]; then
  if [[ ${policy_exit} -eq 3 ]]; then
    reason="$(jq -r '.stop_reason' "${policy_result}")"
    submit_failure path_policy "post-verify repository policy rejected the staged changes" "${reason}"
  else
    submit_failure path_policy "post-verify path policy evaluator failed closed" persistent_failure
  fi
  exit $?
fi
if [[ "${verify_status}" == "failed" ]]; then
  reason="persistent_failure"
  [[ ${verify_exit} -eq 124 ]] && reason="time_limit_reached"
  submit_failure verify "verify_command exited ${verify_exit}" "${reason}"
  exit $?
fi
if [[ -f "${BUDGET_STOP_FILE}" ]]; then
  submit_failure budget "rolling budget stop requested before publication" cost_limit_reached
  exit $?
fi

if git -C "${worktree}" diff --cached --quiet; then
  submit_failure git_publish "executor produced no repository diff" persistent_failure
  exit $?
fi

branch="mission/${code}"
if ! git -C "${worktree}" checkout -B "${branch}" >>"${log_file}" 2>&1 ||
   ! git -C "${worktree}" commit -q -m "mission ${code} (${mtype}, verify=${verify_status})

Auto-generated by rbx-agent-runner on Corbetti.
Executor: ${executor}. Human review and merge required." >>"${log_file}" 2>&1; then
  submit_failure git_publish "mission branch or commit creation failed" persistent_failure
  exit $?
fi
head_commit="$(git -C "${worktree}" rev-parse HEAD)"

if ! git -C "${worktree}" push -u origin "${branch}" >>"${log_file}" 2>&1; then
  submit_failure git_publish "mission branch push failed" persistent_failure
  exit $?
fi

if ! pr_url="$(cd "${worktree}" && gh pr view "${branch}" --json url -q .url 2>>"${log_file}")"; then
  pr_url=""
fi
if [[ -z "${pr_url}" ]]; then
  if ! pr_url="$(cd "${worktree}" && gh pr create \
    --base "${base_branch}" --head "${branch}" --assignee ldamasio \
    --title "mission ${code}: ${mtype}" \
    --body "Auto-generated by the provider-neutral Corbetti runner. Executor: ${executor}. Local verify: ${verify_status}. Maestro result manifests are recorded atomically. Review before merge; merge remains human." \
    2>>"${log_file}")"; then
    pr_url=""
  fi
fi
if [[ -z "${pr_url}" ]]; then
  submit_failure git_publish "pull request creation or lookup failed" persistent_failure
  exit $?
fi

submit_delivery "${branch}" "${head_commit}" "${pr_url}"
