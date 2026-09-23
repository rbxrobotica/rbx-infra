#!/usr/bin/env bash
# Normalize provider-specific coding CLIs behind one Corbetti adapter contract.
# `describe EXECUTOR` prints stable metadata. `run` writes a normalized JSON
# result and returns the provider CLI's exit code (including timeout=124).

set -euo pipefail

usage() {
  echo "usage: $0 describe EXECUTOR | probe EXECUTOR | capabilities | run EXECUTOR WORKTREE PROMPT_FILE LOG_FILE TIMEOUT_S RESULT_FILE RAW_OUTPUT" >&2
  exit 64
}

# cli_for maps an executor to the binary that must exist for it to run at all.
cli_for() {
  case "$1" in
    claude-haiku|claude-sonnet) echo "claude" ;;
    codex) echo "codex" ;;
    glm) echo "glm" ;;
    kimi) echo "kimi" ;;
    *) return 64 ;;
  esac
}

# probe reports only what it actually observed on this host. It never claims an
# executor is usable end to end: credentials, network reachability and quota are
# deliberately NOT probed, because verifying them would mean reading credential
# material or spending a billable call. They are reported as unverified so a
# caller cannot mistake "binary present" for "mission will succeed".
#
# It must never read or print the value of a credential environment variable.
probe() {
  local executor="$1" cli meta available reason version structured help_text
  meta="$(describe "${executor}")" || return $?
  cli="$(cli_for "${executor}")"

  available=false
  reason="cli_not_on_path"
  version=""
  structured=false

  if command -v "${cli}" >/dev/null 2>&1; then
    if version="$(timeout 20 "${cli}" --version 2>/dev/null | head -1 | tr -d '\r')" && [[ -n "${version}" ]]; then
      available=true
      reason="observed"
    else
      reason="cli_version_check_failed"
      version=""
    fi
    # Structured output is what the Corbetti result parser depends on. Probe the
    # advertised interface rather than assuming it from the executor name.
    help_text="$(timeout 20 "${cli}" --help 2>/dev/null || true)"
    case "${executor}" in
      claude-haiku|claude-sonnet|glm)
        if grep -q -- '--output-format' <<<"${help_text}" && grep -q -- '--print' <<<"${help_text}"; then
          structured=true
        fi ;;
      codex)
        grep -q -- '--json' <<<"${help_text}" && structured=true ;;
      kimi)
        structured=false ;;
    esac
    if [[ "${available}" == true && "${structured}" == false && "${executor}" != "kimi" ]]; then
      available=false
      reason="structured_output_unsupported"
    fi
  fi

  jq -n --argjson meta "${meta}" --arg cli "${cli}" --argjson available "${available}" \
    --arg reason "${reason}" --arg version "${version}" --argjson structured "${structured}" \
    '$meta + {
       cli: $cli,
       available: $available,
       reason: $reason,
       cli_version: (if $version == "" then null else $version end),
       structured_output: $structured,
       unverified: ["credentials","network","quota","model_availability"]
     }'
}

# design_capability probes the Claude Design interface available to a headless
# executor on this host. There is no service-credentialed Claude Design API:
# the only interface is the DesignSync tool inside an interactive Claude Code
# session, which authenticates through the owner's claude.ai login and gates
# writes behind a human-reviewed plan. A Corbetti executor therefore cannot use
# it without exposing owner credentials, which the mission contract forbids.
#
# The supported path is export/import: a design bundle is exported from Claude
# Design by the owner, committed to the target repository, and consumed by the
# mission as ordinary repository content.
design_capability() {
  local headless=false reason="no_headless_claude_design_interface_on_host"
  local candidate=false contract_verified=false service_credentialed=false
  local interface="none" owner_login_required=true supported_mode="export_import"
  local cli_version="" probe_fingerprint="" raw_capability="" normalized=""

  # Binary presence is only discovery. Availability requires a narrow,
  # machine-readable RBX capability handshake so an unrelated or interactive
  # executable named `claude-design` cannot silently activate this path.
  if command -v claude-design >/dev/null 2>&1; then
    candidate=true
    reason="headless_contract_unverified"
    if cli_version="$(timeout 20 claude-design --version 2>/dev/null | head -1 | tr -d '\r')" &&
       [[ -n "${cli_version}" ]] &&
       raw_capability="$(timeout 20 claude-design capabilities --json 2>/dev/null)" &&
       normalized="$(jq -cse '
         if length != 1 or .[0].schema_version != "1" then empty else .[0] end
         | select(.interface == "cli" or .interface == "design_sync")
         | select(.headless_available == true)
         | select(.service_credentialed == true)
         | select(.owner_login_required == false)
         | select(.supported_mode == "direct")
         | {schema_version, interface, headless_available,
            service_credentialed, owner_login_required, supported_mode}
       ' <<<"${raw_capability}" 2>/dev/null)" &&
       [[ -n "${normalized}" ]]; then
      headless=true
      contract_verified=true
      service_credentialed=true
      interface="$(jq -r '.interface' <<<"${normalized}")"
      owner_login_required=false
      supported_mode="direct"
      reason="observed_verified_contract"
      probe_fingerprint="$(printf '%s' "${normalized}" | sha256sum | awk '{print $1}')"
    fi
  fi

  jq -n --argjson headless "${headless}" --arg reason "${reason}" \
    --argjson candidate "${candidate}" --argjson verified "${contract_verified}" \
    --argjson service_credentialed "${service_credentialed}" \
    --arg interface "${interface}" --argjson owner_login_required "${owner_login_required}" \
    --arg supported_mode "${supported_mode}" \
    --arg probe_fingerprint "${probe_fingerprint}" \
    '{
       interface: $interface,
       headless_available: $headless,
       reason: $reason,
       candidate_binary_observed: $candidate,
       contract_verified: $verified,
       service_credentialed: $service_credentialed,
       owner_login_required: $owner_login_required,
       supported_mode: $supported_mode,
       degradation: (if $headless then null else "repository_design_bundle" end),
       probe_fingerprint: (if $probe_fingerprint == "" then null else $probe_fingerprint end)
     }'
}

describe() {
  local executor="$1" provider adapter model
  case "${executor}" in
    claude-haiku)
      provider="anthropic"; adapter="claude-cli-v1"; model="${CLAUDE_MODEL:-claude-haiku-4-5-20251001}" ;;
    claude-sonnet)
      provider="anthropic"; adapter="claude-cli-v1"; model="${CLAUDE_SONNET_MODEL:-claude-sonnet-4-6}" ;;
    codex)
      provider="openai"; adapter="codex-cli-v1"; model="${CODEX_MODEL:-configured-default}" ;;
    glm)
      provider="zhipuai"; adapter="glm-cli-v1"; model="${GLM_MODEL:-glm-5.2}" ;;
    kimi)
      provider="moonshot"; adapter="kimi-cli-v1"; model="${KIMI_MODEL:-k2.7}" ;;
    *)
      echo "unknown executor: ${executor}" >&2
      return 64 ;;
  esac
  jq -n --arg executor "${executor}" --arg provider "${provider}" \
    --arg adapter "${adapter}" --arg model "${model}" \
    '{executor:$executor,provider:$provider,adapter:$adapter,model:$model}'
}

[[ $# -ge 1 ]] || usage
mode="$1"

if [[ "${mode}" == "capabilities" ]]; then
  [[ $# -eq 1 ]] || usage
  reports="$(for e in claude-haiku claude-sonnet codex glm kimi; do probe "$e"; done | jq -s '.')"
  jq -n --argjson executors "${reports}" --argjson design "$(design_capability)" \
    '{schema_version:"1", executors:$executors, claude_design:$design}'
  exit 0
fi

[[ $# -ge 2 ]] || usage
executor="$2"

if [[ "${mode}" == "describe" ]]; then
  [[ $# -eq 2 ]] || usage
  describe "${executor}"
  exit $?
fi

if [[ "${mode}" == "probe" ]]; then
  [[ $# -eq 2 ]] || usage
  probe "${executor}"
  exit $?
fi

[[ "${mode}" == "run" && $# -eq 8 ]] || usage
worktree="$3"
prompt_file="$4"
log_file="$5"
timeout_s="$6"
result_file="$7"
raw_output="$8"

meta="$(describe "${executor}")" || exit $?
model="$(jq -r '.model' <<<"${meta}")"
prompt="$(<"${prompt_file}")"
exit_code=0
# Provider processes need their own OAuth/API material, but never the Maestro
# runner credential or the GitHub credential used later by the publication
# stage. This prevents accidental disclosure in tool output and child shells.
sanitized_env=(env -u AGENT_LOOP_RUNNER_KEY -u GITHUB_PAT -u GH_TOKEN -u MAESTRO_URL -u KUBECONFIG)

set +e
(
  cd "${worktree}"
  case "${executor}" in
    claude-haiku|claude-sonnet)
      timeout "${timeout_s}" "${sanitized_env[@]}" claude --print --output-format stream-json --verbose \
        --model "${model}" "${prompt}"
      ;;
    glm)
      timeout "${timeout_s}" "${sanitized_env[@]}" glm --print --output-format stream-json --verbose \
        --model "${model}" "${prompt}"
      ;;
    kimi)
      timeout "${timeout_s}" "${sanitized_env[@]}" kimi -p "${prompt}"
      ;;
    codex)
      codex_args=(exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check --json)
      if [[ -n "${CODEX_MODEL:-}" ]]; then
        codex_args+=(--model "${CODEX_MODEL}")
      fi
      timeout "${timeout_s}" "${sanitized_env[@]}" codex "${codex_args[@]}" "${prompt}"
      ;;
  esac
) >"${raw_output}" 2>&1
exit_code=$?
set -e

cat "${raw_output}" >>"${log_file}"

input_tokens=""
output_tokens=""
if [[ "${executor}" == "claude-haiku" || "${executor}" == "claude-sonnet" || "${executor}" == "glm" ]]; then
  result_line="$(grep -E '"type"[[:space:]]*:[[:space:]]*"result"' "${raw_output}" | tail -1 || true)"
  if [[ -n "${result_line}" ]]; then
    input_tokens="$(jq -r '[.usage.input_tokens,.usage.cache_creation_input_tokens,.usage.cache_read_input_tokens] | map(select(type == "number")) | if length == 0 then empty else add end' <<<"${result_line}" 2>/dev/null || true)"
    output_tokens="$(jq -r '.usage.output_tokens // empty' <<<"${result_line}" 2>/dev/null || true)"
  fi
elif [[ "${executor}" == "codex" ]]; then
  result_line="$(grep -E '"type"[[:space:]]*:[[:space:]]*"turn\.completed"' "${raw_output}" | tail -1 || true)"
  if [[ -n "${result_line}" ]]; then
    input_tokens="$(jq -r '.usage.input_tokens // empty' <<<"${result_line}" 2>/dev/null || true)"
    output_tokens="$(jq -r '.usage.output_tokens // empty' <<<"${result_line}" 2>/dev/null || true)"
  fi
fi

[[ "${input_tokens}" =~ ^[0-9]+$ ]] || input_tokens="null"
[[ "${output_tokens}" =~ ^[0-9]+$ ]] || output_tokens="null"

jq -n --argjson meta "${meta}" --argjson exit_code "${exit_code}" \
  --argjson input_tokens "${input_tokens}" --argjson output_tokens "${output_tokens}" \
  '$meta + {exit_code:$exit_code,input_tokens:$input_tokens,output_tokens:$output_tokens}' \
  >"${result_file}"

exit "${exit_code}"
