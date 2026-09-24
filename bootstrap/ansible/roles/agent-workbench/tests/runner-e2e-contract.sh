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

# The per-mission capability probe calls every executor CLI with --version and
# --help, so the fake must answer those without touching the tree.
cat >"${tmp_dir}/fakebin/codex" <<'SH'
#!/usr/bin/env bash
case "${1:-}" in
  --version) echo "0.0.0 (fake codex)"; exit 0 ;;
  --help) echo "  --json"; exit 0 ;;
esac
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

# Missions 3 and 4 assert the "no headless Claude Design interface" path, which
# requires that no claude-design binary is visible. Fail loudly instead of
# reporting a flaky result if the host has one.
if command -v claude-design >/dev/null 2>&1; then
  echo "runner-e2e-contract requires no claude-design binary on PATH" >&2
  exit 1
fi

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
# A contract without `capabilities` keeps the legacy delivery shape, byte for
# byte: neither a capabilities nor a design key, and exactly the legacy key set.
jq -e '(.delivery | has("design") | not) and (.delivery | has("capabilities") | not)
       and (.delivery | keys == ["branch","changed_files","diff_files","diff_lines","head_commit","outcome","path_policy","pull_request_url","schema_version","verify"])' \
  "${CAPTURE_RESULT}" >/dev/null
test -s "${HOME}/rbx/manifests/mission-2026-00001/capability.json"
test ! -e "${HOME}/rbx/manifests/mission-2026-00001/capabilities.json"
jq -e '.schema_version == "1" and (.claude_design | type == "object")' \
  "${HOME}/rbx/manifests/mission-2026-00001/capability.json" >/dev/null
# The stored probe must validate against the owned capability-report schema.
validate_capability_report() {
  python3 - "${role_dir}/schemas/capability-report.v1.schema.json" "$1" <<'PYV'
import json, sys
try:
    import jsonschema
except ImportError:  # the schema is still checked for well-formedness by the delivery contract
    sys.exit(0)
schema = json.load(open(sys.argv[1]))
jsonschema.Draft202012Validator.check_schema(schema)
jsonschema.Draft202012Validator(schema).validate(json.load(open(sys.argv[2])))
PYV
}
validate_capability_report "${HOME}/rbx/manifests/mission-2026-00001/capability.json"
git --git-dir="${tmp_dir}/origin.git" show-ref --verify --quiet refs/heads/mission/mission-2026-00001
test ! -e "${HOME}/rbx/worktrees/mission-2026-00001"

# A second mission writes a forbidden path. It must report a FailureManifest,
# never push a mission branch, and still clean up after Maestro acknowledges it.
cat >"${tmp_dir}/fakebin/codex" <<'SH'
#!/usr/bin/env bash
case "${1:-}" in
  --version) echo "0.0.0 (fake codex)"; exit 0 ;;
  --help) echo "  --json"; exit 0 ;;
esac
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

# A third mission requires Claude Design and permits the repository_design_bundle
# degradation. No claude-design binary exists, so the executor must proceed in
# degraded mode, the prompt must say so, and the delivery must record
# capabilities.design.mode == "degraded" with the consumed bundle's path and its
# hash at the mission head. No `design` attestation is emitted in degraded mode.
design_mission_dir="missions/flightdeck-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
cat >"${tmp_dir}/fakebin/codex" <<SH
#!/usr/bin/env bash
case "\${1:-}" in
  --version) echo "0.0.0 (fake codex)"; exit 0 ;;
  --help) echo "  --json"; exit 0 ;;
esac
mkdir -p "${design_mission_dir}/design" "${design_mission_dir}/out"
printf '{"bundle":"owner-export","tokens":{"accent":"#00c2ff"}}\\n' > "${design_mission_dir}/design/bundle.json"
printf 'rendered\\n' > "${design_mission_dir}/out/post.txt"
echo '{"type":"turn.completed","usage":{"input_tokens":7,"output_tokens":8}}'
SH
chmod +x "${tmp_dir}/fakebin/codex"
jq --arg dir "${design_mission_dir}" '
  . + {allowed_paths:[($dir + "/**")],
       verify_command:("test -f " + $dir + "/design/bundle.json"),
       capabilities:{design:{required:true,degraded_mode:"repository_design_bundle"}}}' \
  "${tmp_dir}/contract.json" >"${tmp_dir}/contract.design-bundle.json"
export CAPTURE_RESULT="${tmp_dir}/design-bundle-result.json"
"$executor" mission-2026-00003 "${tmp_dir}/contract.design-bundle.json" \
  55555555-5555-4555-8555-555555555555 66666666-6666-4666-8666-666666666666 1

expected_bundle_hash="$(printf '{"bundle":"owner-export","tokens":{"accent":"#00c2ff"}}\n' | sha256sum | awk '{print $1}')"
jq -e --arg dir "${design_mission_dir}" --arg hash "${expected_bundle_hash}" '
  .delivery.outcome == "delivered"
  and (.delivery | has("design") | not)
  and .delivery.capabilities.design.required == true
  and .delivery.capabilities.design.observed == false
  and .delivery.capabilities.design.mode == "degraded"
  and .delivery.capabilities.design.degraded_mode == "repository_design_bundle"
  and .delivery.capabilities.design.bundle_ref == ($dir + "/design/bundle.json")
  and (.delivery.capabilities.design.bundle_hash | test("^[0-9a-f]{64}$"))
  and .delivery.capabilities.design.bundle_hash == $hash
  and (.delivery.capabilities.design.evidence_ref | test("^capability:[0-9a-f]{64}$"))
  and (.delivery.capabilities | keys == ["design"])' "${CAPTURE_RESULT}" >/dev/null
jq -e '.design.mode == "degraded"' "${HOME}/rbx/manifests/mission-2026-00003/capabilities.json" >/dev/null
jq -e '. == null' "${HOME}/rbx/manifests/mission-2026-00003/design.json" >/dev/null
grep -q 'Claude Design is unavailable on this host' "${HOME}/rbx/manifests/mission-2026-00003/prompt.txt"
grep -Fq "${design_mission_dir}/design/" "${HOME}/rbx/manifests/mission-2026-00003/prompt.txt"
if grep -Eq 'test-key|test-token' "${HOME}/rbx/manifests/mission-2026-00003/prompt.txt"; then
  echo "prompt leaked credential material" >&2
  exit 1
fi
git --git-dir="${tmp_dir}/origin.git" show-ref --verify --quiet refs/heads/mission/mission-2026-00003
test ! -e "${HOME}/rbx/worktrees/mission-2026-00003"

# A fourth mission requires Claude Design with no degraded mode permitted. On a
# host without a headless interface it must fail closed in the capability
# phase, before the executor runs, and push no branch.
jq '.capabilities.design.degraded_mode = null' \
  "${tmp_dir}/contract.design-bundle.json" >"${tmp_dir}/contract.design-strict.json"
rm -f "${tmp_dir}/codex-invoked"
cat >"${tmp_dir}/fakebin/codex" <<SH
#!/usr/bin/env bash
case "\${1:-}" in
  --version) echo "0.0.0 (fake codex)"; exit 0 ;;
  --help) echo "  --json"; exit 0 ;;
esac
touch "${tmp_dir}/codex-invoked"
echo '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}'
SH
chmod +x "${tmp_dir}/fakebin/codex"
export CAPTURE_RESULT="${tmp_dir}/design-strict-result.json"
"$executor" mission-2026-00004 "${tmp_dir}/contract.design-strict.json" \
  77777777-7777-4777-8777-777777777777 88888888-8888-4888-8888-888888888888 1

jq -e '.failure.outcome == "stopped" and .failure.phase == "capability"
       and .failure.stop_reason == "persistent_failure"
       and (.execution.base_commit | test("^[0-9a-f]{40}$"))' "${CAPTURE_RESULT}" >/dev/null
test ! -e "${tmp_dir}/codex-invoked"
if git --git-dir="${tmp_dir}/origin.git" show-ref --verify --quiet refs/heads/mission/mission-2026-00004; then
  echo "capability-gated mission unexpectedly pushed a branch" >&2
  exit 1
fi
test ! -e "${HOME}/rbx/worktrees/mission-2026-00004"

# Missions 5 to 7 run against a verified headless claude-design handshake.
cat >"${tmp_dir}/fakebin/claude-design" <<'SH'
#!/usr/bin/env bash
if [[ "${1:-}" == "--version" ]]; then
  echo "1.2.3 (Contract Test)"
elif [[ "${1:-}" == "capabilities" && "${2:-}" == "--json" ]]; then
  printf '%s\n' '{"schema_version":"1","interface":"cli","headless_available":true,"service_credentialed":true,"owner_login_required":false,"supported_mode":"direct"}'
else
  exit 64
fi
SH
chmod +x "${tmp_dir}/fakebin/claude-design"

# A fifth mission observes the interface but the executor leaves no attestation
# and no bundle. Observation is not use: the delivery records mode "degraded"
# (the contract permits repository_design_bundle) with no bundle fields and no
# `design` attestation. evidence_ref must carry the probe fingerprint.
cat >"${tmp_dir}/fakebin/codex" <<SH
#!/usr/bin/env bash
case "\${1:-}" in
  --version) echo "0.0.0 (fake codex)"; exit 0 ;;
  --help) echo "  --json"; exit 0 ;;
esac
mkdir -p "${design_mission_dir}/out"
printf 'rendered\\n' > "${design_mission_dir}/out/post.txt"
echo '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}'
SH
chmod +x "${tmp_dir}/fakebin/codex"
jq --arg dir "${design_mission_dir}" '.verify_command = ("test -f " + $dir + "/out/post.txt")' \
  "${tmp_dir}/contract.design-bundle.json" >"${tmp_dir}/contract.design-observed.json"
export CAPTURE_RESULT="${tmp_dir}/design-observed-result.json"
"$executor" mission-2026-00005 "${tmp_dir}/contract.design-observed.json" \
  99999999-9999-4999-8999-999999999999 aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa 1

probe_fingerprint="$(jq -r '.claude_design.probe_fingerprint' "${HOME}/rbx/manifests/mission-2026-00005/capability.json")"
validate_capability_report "${HOME}/rbx/manifests/mission-2026-00005/capability.json"
jq -e --arg fp "${probe_fingerprint}" '
  .delivery.outcome == "delivered"
  and (.delivery | has("design") | not)
  and .delivery.capabilities.design.observed == true
  and .delivery.capabilities.design.mode == "degraded"
  and .delivery.capabilities.design.degraded_mode == "repository_design_bundle"
  and .delivery.capabilities.design.required == true
  and (.delivery.capabilities.design | has("bundle_ref") | not)
  and (.delivery.capabilities.design | has("bundle_hash") | not)
  and .delivery.capabilities.design.evidence_ref == ("capability:" + $fp)' "${CAPTURE_RESULT}" >/dev/null
if grep -q 'Claude Design is unavailable on this host' "${HOME}/rbx/manifests/mission-2026-00005/prompt.txt"; then
  echo "degraded-mode prompt emitted although a headless interface was observed" >&2
  exit 1
fi
grep -Fq "${probe_fingerprint}" "${HOME}/rbx/manifests/mission-2026-00005/prompt.txt"
grep -Fq "${design_mission_dir}/design/attestation.json" "${HOME}/rbx/manifests/mission-2026-00005/prompt.txt"
test ! -e "${HOME}/rbx/worktrees/mission-2026-00005"

# A sixth mission observes the interface AND the executor stages a matching
# attestation. Only now is `design.status == "used"` emitted, with mode "direct".
# A bundle is staged too: direct mode must omit bundle_ref/bundle_hash and
# degraded_mode, because Maestro fails closed on direct plus bundle.
cat >"${tmp_dir}/fakebin/codex" <<SH
#!/usr/bin/env bash
case "\${1:-}" in
  --version) echo "0.0.0 (fake codex)"; exit 0 ;;
  --help) echo "  --json"; exit 0 ;;
esac
mkdir -p "${design_mission_dir}/design" "${design_mission_dir}/out"
printf '{"interface":"cli","probe_fingerprint":"%s"}\\n' "${probe_fingerprint}" > "${design_mission_dir}/design/attestation.json"
printf '{"bundle":"also-present"}\\n' > "${design_mission_dir}/design/bundle.json"
printf 'rendered\\n' > "${design_mission_dir}/out/post.txt"
echo '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}'
SH
chmod +x "${tmp_dir}/fakebin/codex"
export CAPTURE_RESULT="${tmp_dir}/design-direct-result.json"
"$executor" mission-2026-00006 "${tmp_dir}/contract.design-observed.json" \
  bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb cccccccc-cccc-4ccc-8ccc-cccccccccccc 1

jq -e --arg fp "${probe_fingerprint}" --arg dir "${design_mission_dir}" '
  .delivery.outcome == "delivered"
  and .delivery.design.status == "used"
  and .delivery.design.interface == "cli"
  and .delivery.design.evidence_ref == ("capability:" + $fp)
  and (.delivery.design | keys == ["evidence_ref","interface","status"])
  and .delivery.capabilities.design.mode == "direct"
  and .delivery.capabilities.design.observed == true
  and .delivery.capabilities.design.required == true
  and (.delivery.capabilities.design | has("bundle_ref") | not)
  and (.delivery.capabilities.design | has("bundle_hash") | not)
  and (.delivery.capabilities.design | has("degraded_mode") | not)
  and (.delivery.capabilities.design | keys == ["evidence_ref","mode","observed","required"])
  and (.delivery.changed_files | index($dir + "/design/attestation.json"))
  and (.delivery.changed_files | index($dir + "/design/bundle.json"))' "${CAPTURE_RESULT}" >/dev/null
jq -e '.status == "used"' "${HOME}/rbx/manifests/mission-2026-00006/design.json" >/dev/null
git --git-dir="${tmp_dir}/origin.git" show-ref --verify --quiet refs/heads/mission/mission-2026-00006
test ! -e "${HOME}/rbx/worktrees/mission-2026-00006"

# A seventh mission observes the interface, requires Design, permits no
# degradation, and the executor writes an attestation with a fingerprint that
# does not match the probe. Forged or stale evidence is not evidence: the
# mission must fail closed in the capability phase with no branch pushed.
forged_fingerprint="$(printf 'x%.0s' {1..64})"
cat >"${tmp_dir}/fakebin/codex" <<SH
#!/usr/bin/env bash
case "\${1:-}" in
  --version) echo "0.0.0 (fake codex)"; exit 0 ;;
  --help) echo "  --json"; exit 0 ;;
esac
mkdir -p "${design_mission_dir}/design" "${design_mission_dir}/out"
printf '{"interface":"cli","probe_fingerprint":"%s"}\\n' "${forged_fingerprint}" > "${design_mission_dir}/design/attestation.json"
printf 'rendered\\n' > "${design_mission_dir}/out/post.txt"
echo '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}'
SH
chmod +x "${tmp_dir}/fakebin/codex"
jq '.capabilities.design.degraded_mode = null' \
  "${tmp_dir}/contract.design-observed.json" >"${tmp_dir}/contract.design-observed-strict.json"
export CAPTURE_RESULT="${tmp_dir}/design-forged-result.json"
"$executor" mission-2026-00007 "${tmp_dir}/contract.design-observed-strict.json" \
  dddddddd-dddd-4ddd-8ddd-dddddddddddd eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee 1

jq -e '.failure.outcome == "stopped" and .failure.phase == "capability"
       and .failure.stop_reason == "persistent_failure"' "${CAPTURE_RESULT}" >/dev/null
if git --git-dir="${tmp_dir}/origin.git" show-ref --verify --quiet refs/heads/mission/mission-2026-00007; then
  echo "unattested strict design mission unexpectedly pushed a branch" >&2
  exit 1
fi
rm -f "${tmp_dir}/fakebin/claude-design"
test ! -e "${HOME}/rbx/worktrees/mission-2026-00007"

# An eighth mission declares Design optional on a host without the interface.
# The delivery records mode "unavailable" with observed false, required echoed
# as false, no degraded_mode key, no bundle keys and no `design` attestation.
cat >"${tmp_dir}/fakebin/codex" <<SH
#!/usr/bin/env bash
case "\${1:-}" in
  --version) echo "0.0.0 (fake codex)"; exit 0 ;;
  --help) echo "  --json"; exit 0 ;;
esac
mkdir -p "${design_mission_dir}/out"
printf 'rendered\\n' > "${design_mission_dir}/out/post.txt"
echo '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}'
SH
chmod +x "${tmp_dir}/fakebin/codex"
jq '.capabilities.design = {required:false,degraded_mode:null}' \
  "${tmp_dir}/contract.design-observed.json" >"${tmp_dir}/contract.design-optional.json"
export CAPTURE_RESULT="${tmp_dir}/design-optional-result.json"
"$executor" mission-2026-00008 "${tmp_dir}/contract.design-optional.json" \
  ffffffff-ffff-4fff-8fff-ffffffffffff 12121212-1212-4121-8121-121212121212 1

jq -e '.delivery.outcome == "delivered"
       and (.delivery | has("design") | not)
       and .delivery.capabilities.design.required == false
       and .delivery.capabilities.design.observed == false
       and .delivery.capabilities.design.mode == "unavailable"
       and (.delivery.capabilities.design | has("degraded_mode") | not)
       and (.delivery.capabilities.design | keys == ["evidence_ref","mode","observed","required"])
       and (.delivery.capabilities.design.evidence_ref | test("^capability:[0-9a-f]{64}$"))' "${CAPTURE_RESULT}" >/dev/null
test ! -e "${HOME}/rbx/worktrees/mission-2026-00008"

# A ninth mission observes the interface and stages attestation.json as a
# symlink to an out-of-tree JSON carrying the correct fingerprint. Evidence is
# read from the staged Git object, and a 120000 symlink entry is not a regular
# blob: no `design` attestation, no bundle, and the permitted degraded mode.
cat >"${tmp_dir}/fakebin/claude-design" <<'SH'
#!/usr/bin/env bash
if [[ "${1:-}" == "--version" ]]; then
  echo "1.2.3 (Contract Test)"
elif [[ "${1:-}" == "capabilities" && "${2:-}" == "--json" ]]; then
  printf '%s\n' '{"schema_version":"1","interface":"cli","headless_available":true,"service_credentialed":true,"owner_login_required":false,"supported_mode":"direct"}'
else
  exit 64
fi
SH
printf '{"interface":"cli","probe_fingerprint":"%s"}\n' "${probe_fingerprint}" >"${tmp_dir}/outside-attestation.json"
cat >"${tmp_dir}/fakebin/codex" <<SH
#!/usr/bin/env bash
case "\${1:-}" in
  --version) echo "0.0.0 (fake codex)"; exit 0 ;;
  --help) echo "  --json"; exit 0 ;;
esac
mkdir -p "${design_mission_dir}/design" "${design_mission_dir}/out"
ln -s "${tmp_dir}/outside-attestation.json" "${design_mission_dir}/design/attestation.json"
printf 'rendered\\n' > "${design_mission_dir}/out/post.txt"
echo '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}'
SH
chmod +x "${tmp_dir}/fakebin/claude-design" "${tmp_dir}/fakebin/codex"
export CAPTURE_RESULT="${tmp_dir}/design-symlink-result.json"
"$executor" mission-2026-00009 "${tmp_dir}/contract.design-observed.json" \
  34343434-3434-4343-8343-343434343434 56565656-5656-4565-8565-565656565656 1

jq -e --arg fp "${probe_fingerprint}" --arg dir "${design_mission_dir}" '
  .delivery.outcome == "delivered"
  and (.delivery | has("design") | not)
  and .delivery.capabilities.design.observed == true
  and .delivery.capabilities.design.mode == "degraded"
  and .delivery.capabilities.design.degraded_mode == "repository_design_bundle"
  and (.delivery.capabilities.design | has("bundle_ref") | not)
  and (.delivery.capabilities.design | has("bundle_hash") | not)
  and .delivery.capabilities.design.evidence_ref == ("capability:" + $fp)
  and (.delivery.changed_files | index($dir + "/design/attestation.json"))' "${CAPTURE_RESULT}" >/dev/null
jq -e '. == null' "${HOME}/rbx/manifests/mission-2026-00009/design.json" >/dev/null
grep -q 'is not a staged regular file (symlink or non-blob); not counted as evidence' "${HOME}/rbx/logs/mission-2026-00009.log"
# The pushed mission commit really holds a symlink entry, proving the test
# exercised the staged-object rule rather than a missing file.
test "$(git --git-dir="${tmp_dir}/origin.git" ls-tree refs/heads/mission/mission-2026-00009 \
  "${design_mission_dir}/design/attestation.json" | awk '{print $1}')" = 120000
rm -f "${tmp_dir}/fakebin/claude-design"
test ! -e "${HOME}/rbx/worktrees/mission-2026-00009"

echo "runner E2E contract: ok"
