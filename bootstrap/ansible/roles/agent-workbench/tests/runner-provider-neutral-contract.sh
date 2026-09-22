#!/usr/bin/env bash
set -euo pipefail

role_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
adapter="${role_dir}/files/rbx-executor-adapter.sh"
policy="${role_dir}/files/rbx-mission-policy.py"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

git init -q -b main "${tmp_dir}/repo"
git -C "${tmp_dir}/repo" config user.name test
git -C "${tmp_dir}/repo" config user.email test@example.invalid
mkdir -p "${tmp_dir}/repo/src" "${tmp_dir}/repo/secrets"
printf 'base\n' >"${tmp_dir}/repo/src/a.txt"
git -C "${tmp_dir}/repo" add .
git -C "${tmp_dir}/repo" commit -qm base

cat >"${tmp_dir}/contract.json" <<'JSON'
{"allowed_paths":["src/**"],"forbidden_paths":["secrets/**","**/*.env*"],"max_diff_size":"5 lines"}
JSON

printf 'change\n' >>"${tmp_dir}/repo/src/a.txt"
git -C "${tmp_dir}/repo" add -A
python3 "$policy" "${tmp_dir}/contract.json" "${tmp_dir}/repo" "${tmp_dir}/policy.json"
jq -e '.status == "passed" and .changed_files == ["src/a.txt"] and .diff_lines == 1' "${tmp_dir}/policy.json" >/dev/null

printf 'secret\n' >"${tmp_dir}/repo/secrets/key.txt"
git -C "${tmp_dir}/repo" add -A
set +e
python3 "$policy" "${tmp_dir}/contract.json" "${tmp_dir}/repo" "${tmp_dir}/policy.json"
policy_rc=$?
set -e
test "$policy_rc" -eq 3
jq -e '.status == "failed" and .stop_reason == "forbidden_action_attempted" and (.violations | index("secrets/key.txt:forbidden_path"))' "${tmp_dir}/policy.json" >/dev/null

mkdir -p "${tmp_dir}/bin" "${tmp_dir}/worktree"
printf 'prompt\n' >"${tmp_dir}/prompt.txt"
cat >"${tmp_dir}/bin/claude" <<'SH'
#!/usr/bin/env bash
echo '{"type":"result","usage":{"input_tokens":2,"cache_read_input_tokens":3,"output_tokens":4}}'
SH
cp "${tmp_dir}/bin/claude" "${tmp_dir}/bin/glm"
cat >"${tmp_dir}/bin/codex" <<'SH'
#!/usr/bin/env bash
echo '{"type":"turn.completed","usage":{"input_tokens":5,"output_tokens":6}}'
SH
cat >"${tmp_dir}/bin/kimi" <<'SH'
#!/usr/bin/env bash
echo 'kimi complete'
SH
chmod +x "${tmp_dir}/bin/"*

for executor in claude-haiku claude-sonnet glm codex kimi; do
  PATH="${tmp_dir}/bin:${PATH}" "$adapter" run "$executor" "${tmp_dir}/worktree" \
    "${tmp_dir}/prompt.txt" "${tmp_dir}/adapter.log" 10 \
    "${tmp_dir}/${executor}.json" "${tmp_dir}/${executor}.raw"
  jq -e --arg executor "$executor" '.executor == $executor and (.adapter | length > 0) and (.provider | length > 0)' \
    "${tmp_dir}/${executor}.json" >/dev/null
done
jq -e '.input_tokens == 5 and .output_tokens == 4' "${tmp_dir}/claude-haiku.json" >/dev/null
jq -e '.input_tokens == 5 and .output_tokens == 6' "${tmp_dir}/codex.json" >/dev/null
jq -e '.input_tokens == null and .output_tokens == null' "${tmp_dir}/kimi.json" >/dev/null

if "$adapter" describe unknown >/dev/null 2>&1; then
  echo "unknown executor must fail closed" >&2
  exit 1
fi

echo "provider-neutral runner contracts: ok"
