#!/usr/bin/env bash
# Contract for `rbx-executor-adapter.sh probe|capabilities`.
#
# The probe exists so Corbetti never admits a creative mission against an
# executor that is not actually present. These tests pin the two failure modes
# that matter: claiming availability that was not observed, and leaking
# credential material into a capability report.
set -euo pipefail

role_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
adapter="${role_dir}/files/rbx-executor-adapter.sh"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

mkdir -p "${tmp_dir}/bin"

# Isolate PATH down to coreutils plus a deps dir holding only jq, so no provider
# CLI is visible unless a test explicitly puts one in ${tmp_dir}/bin. Adding jq's
# real directory wholesale would also expose the operator's own `claude`.
mkdir -p "${tmp_dir}/deps"
ln -s "$(command -v jq)" "${tmp_dir}/deps/jq"
empty_path="${tmp_dir}/bin:${tmp_dir}/deps:/usr/bin:/bin"

# 1. Absent CLI must report unavailable, never "observed".
for executor in claude-haiku claude-sonnet codex glm kimi; do
  PATH="${empty_path}" "$adapter" probe "$executor" >"${tmp_dir}/absent.json"
  jq -e --arg e "$executor" '
    .executor == $e and .available == false and .reason == "cli_not_on_path"
    and .cli_version == null' "${tmp_dir}/absent.json" >/dev/null
done

# 2. A present CLI that advertises structured output is available, and the
#    reported version is the one the binary actually printed.
cat >"${tmp_dir}/bin/claude" <<'SH'
#!/usr/bin/env bash
case "${1:-}" in
  --version) echo "9.9.9 (Fake Claude Code)" ;;
  --help) echo "  --print  ...";  echo "  --output-format <format>  ..." ;;
esac
SH
chmod +x "${tmp_dir}/bin/claude"
PATH="${empty_path}" "$adapter" probe claude-sonnet >"${tmp_dir}/present.json"
jq -e '.available == true and .reason == "observed" and .structured_output == true
       and .cli_version == "9.9.9 (Fake Claude Code)"' "${tmp_dir}/present.json" >/dev/null

# 3. A present CLI WITHOUT structured output must not be reported available:
#    the Corbetti result parser depends on it, so presence alone is not enough.
cat >"${tmp_dir}/bin/claude" <<'SH'
#!/usr/bin/env bash
case "${1:-}" in
  --version) echo "0.0.1 (No Structured Output)" ;;
  --help) echo "  --interactive-only" ;;
esac
SH
chmod +x "${tmp_dir}/bin/claude"
PATH="${empty_path}" "$adapter" probe claude-sonnet >"${tmp_dir}/nostruct.json"
jq -e '.available == false and .reason == "structured_output_unsupported"
       and .structured_output == false' "${tmp_dir}/nostruct.json" >/dev/null

# 4. A CLI that fails its version check is not available.
cat >"${tmp_dir}/bin/claude" <<'SH'
#!/usr/bin/env bash
exit 1
SH
chmod +x "${tmp_dir}/bin/claude"
PATH="${empty_path}" "$adapter" probe claude-sonnet >"${tmp_dir}/badversion.json"
jq -e '.available == false and .reason == "cli_version_check_failed"' "${tmp_dir}/badversion.json" >/dev/null

# 5. Credentials, network and quota are always declared unverified. The probe
#    must not upgrade "binary present" into "mission will succeed".
jq -e '(.unverified | index("credentials")) and (.unverified | index("network"))
       and (.unverified | index("quota"))' "${tmp_dir}/present.json" >/dev/null

# 6. A capability report must never contain credential material. Export decoy
#    credentials and assert none of them appear anywhere in the output.
rm -f "${tmp_dir}/bin/claude"
report="$(
  PATH="${empty_path}" \
  ANTHROPIC_API_KEY="decoy-anthropic-must-not-appear" \
  GEMINI_API_KEY="decoy-gemini-must-not-appear" \
  GITHUB_PAT="decoy-github-must-not-appear" \
  AGENT_LOOP_RUNNER_KEY="decoy-runner-must-not-appear" \
  "$adapter" capabilities
)"
for decoy in decoy-anthropic decoy-gemini decoy-github decoy-runner; do
  if grep -q "${decoy}" <<<"${report}"; then
    echo "capability report leaked credential material: ${decoy}" >&2
    exit 1
  fi
done

# 7. Claude Design has no headless interface on a host without a claude-design
#    binary. The report must say so explicitly and name the degraded mode,
#    rather than implying Design participated.
jq -e '.claude_design.interface == "none"
       and .claude_design.headless_available == false
       and .claude_design.owner_login_required == true
       and .claude_design.supported_mode == "export_import"
       and .claude_design.degradation == "repository_design_bundle"' <<<"${report}" >/dev/null

# 8. The capability report covers every executor the adapter can describe.
jq -e '.schema_version == "1" and (.executors | length == 5)
       and ([.executors[].executor] | sort
            == ["claude-haiku","claude-sonnet","codex","glm","kimi"])' <<<"${report}" >/dev/null

# 9. An unknown executor is rejected, not invented.
set +e
PATH="${empty_path}" "$adapter" probe not-a-real-executor >/dev/null 2>&1
rc=$?
set -e
test "$rc" -eq 64

echo "runner-capability-probe-contract: all assertions passed"
