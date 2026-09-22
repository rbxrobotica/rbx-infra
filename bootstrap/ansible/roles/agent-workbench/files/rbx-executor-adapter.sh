#!/usr/bin/env bash
# Normalize provider-specific coding CLIs behind one Corbetti adapter contract.
# `describe EXECUTOR` prints stable metadata. `run` writes a normalized JSON
# result and returns the provider CLI's exit code (including timeout=124).

set -euo pipefail

usage() {
  echo "usage: $0 describe EXECUTOR | run EXECUTOR WORKTREE PROMPT_FILE LOG_FILE TIMEOUT_S RESULT_FILE" >&2
  exit 64
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

[[ $# -ge 2 ]] || usage
mode="$1"
executor="$2"

if [[ "${mode}" == "describe" ]]; then
  [[ $# -eq 2 ]] || usage
  describe "${executor}"
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
