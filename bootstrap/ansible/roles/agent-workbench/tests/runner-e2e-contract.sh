#!/usr/bin/env bash
set -euo pipefail

role_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
executor="${role_dir}/files/rbx-mission-executor.sh"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

export HOME="${tmp_dir}/home"
export RUNNER_ID="corbetti-test"
export RUNNER_GIT_AUTHOR_NAME="RBX Test Runner"
export RUNNER_GIT_AUTHOR_EMAIL="runner@example.invalid"
export AGENT_LOOP_RUNNER_KEY="test-key"
export MAESTRO_URL="https://maestro.example.invalid/api/v1/agent-loop"
export GITHUB_PAT="test-token"
mkdir -p "${HOME}/rbx/repos/rbxrobotica" "${HOME}/rbx/bin" "${tmp_dir}/source/src" "${tmp_dir}/fakebin"

git init -q --bare "${tmp_dir}/origin.git"
git init -q -b main "${tmp_dir}/source"
git -C "${tmp_dir}/source" config user.name test
git -C "${tmp_dir}/source" config user.email test@example.invalid
printf 'base\n' >"${tmp_dir}/source/src/base.txt"
git -C "${tmp_dir}/source" add .
git -C "${tmp_dir}/source" commit -qm base
source_commit="$(git -C "${tmp_dir}/source" rev-parse HEAD)"
git -C "${tmp_dir}/source" remote add origin "${tmp_dir}/origin.git"
git -C "${tmp_dir}/source" push -q -u origin main
git clone -q --bare "${tmp_dir}/origin.git" "${HOME}/rbx/repos/rbxrobotica/demo.git"

cat >"${tmp_dir}/fakebin/codex" <<'SH'
#!/usr/bin/env bash
mkdir -p src
printf 'generated\n' > src/generated.txt
echo '{"type":"turn.completed","usage":{"input_tokens":5,"output_tokens":6}}'
SH
cat >"${tmp_dir}/fakebin/gh" <<'SH'
#!/usr/bin/env bash
if [[ "$1 $2" == "pr view" ]]; then
  echo 'https://github.com/rbxrobotica/demo/pull/77'
  exit 0
fi
echo 'unexpected gh invocation' >&2
exit 2
SH
cat >"${tmp_dir}/fakebin/curl" <<'SH'
#!/usr/bin/env bash
output=""
data_file=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o) output="$2"; shift 2 ;;
    --data-binary) data_file="${2#@}"; shift 2 ;;
    *) shift ;;
  esac
done
cp "$data_file" "${CAPTURE_RESULT}"
printf '{"status":"accepted"}\n' >"$output"
printf '201'
SH
chmod +x "${tmp_dir}/fakebin/"*
export PATH="${tmp_dir}/fakebin:${PATH}"

cat >"${tmp_dir}/contract.json" <<'JSON'
{
  "type":"feature-loop",
  "repo":"rbxrobotica/demo",
  "base_branch":"main",
  "objective":"Create one generated file to prove the repository-bound runner pipeline.",
  "allowed_paths":["src/**"],
  "forbidden_paths":["secrets/**","**/*.env*"],
  "done_criteria":["generated file exists"],
  "verify_command":"test -f src/generated.txt",
  "max_runtime":"PT2M",
  "max_cost":"100 tokens",
  "max_diff_size":"10 lines",
  "executor":"codex"
}
JSON
jq --arg source_commit "${source_commit}" '. + {source_commit:$source_commit}' \
  "${tmp_dir}/contract.json" >"${tmp_dir}/contract.with-source.json"
mv "${tmp_dir}/contract.with-source.json" "${tmp_dir}/contract.json"

export CAPTURE_RESULT="${tmp_dir}/delivery-result.json"
"$executor" mission-2026-00001 "${tmp_dir}/contract.json" \
  11111111-1111-4111-8111-111111111111 22222222-2222-4222-8222-222222222222 1

jq -e '.delivery.outcome == "delivered" and .delivery.path_policy.status == "passed" and .delivery.verify.status == "passed" and .execution.provider == "openai" and .execution.input_tokens == 5' \
  "${CAPTURE_RESULT}" >/dev/null
jq -e --arg source_commit "${source_commit}" '.execution.base_commit == $source_commit' \
  "${CAPTURE_RESULT}" >/dev/null
git --git-dir="${tmp_dir}/origin.git" show-ref --verify --quiet refs/heads/mission/mission-2026-00001
test ! -e "${HOME}/rbx/worktrees/mission-2026-00001"

# A second mission writes a forbidden path. It must report a FailureManifest,
# never push a mission branch, and still clean up after Maestro acknowledges it.
cat >"${tmp_dir}/fakebin/codex" <<'SH'
#!/usr/bin/env bash
mkdir -p secrets
printf 'blocked\n' > secrets/key.txt
echo '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}'
SH
chmod +x "${tmp_dir}/fakebin/codex"
export CAPTURE_RESULT="${tmp_dir}/failure-result.json"
"$executor" mission-2026-00002 "${tmp_dir}/contract.json" \
  33333333-3333-4333-8333-333333333333 44444444-4444-4444-8444-444444444444 1

jq -e '.failure.outcome == "stopped" and .failure.stop_reason == "forbidden_action_attempted" and .failure.phase == "path_policy"' \
  "${CAPTURE_RESULT}" >/dev/null
if git --git-dir="${tmp_dir}/origin.git" show-ref --verify --quiet refs/heads/mission/mission-2026-00002; then
  echo "forbidden mission unexpectedly pushed a branch" >&2
  exit 1
fi
test ! -e "${HOME}/rbx/worktrees/mission-2026-00002"

echo "runner E2E contract: ok"
