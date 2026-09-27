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

# A detected rename must authorize the removed source and the added destination.
# Keep NUL-delimited filenames intact, including tabs and newlines, and count
# both affected paths for file bounds without inflating Git's line statistics.
check_rename_policy() {
  local label="$1" source="$2" destination="$3" expected_rc="$4" violation="$5"
  local bound="${6:-5 lines}" rename_repo="${tmp_dir}/rename-${1}" actual_rc=0
  git init -q -b main "${rename_repo}"
  git -C "${rename_repo}" config user.name test
  git -C "${rename_repo}" config user.email test@example.invalid
  git -C "${rename_repo}" config diff.renames true
  mkdir -p "${rename_repo}/$(dirname "${source}")" "${rename_repo}/$(dirname "${destination}")"
  printf 'rename fixture\n' >"${rename_repo}/${source}"
  git -C "${rename_repo}" add -A
  git -C "${rename_repo}" commit -qm base
  git -C "${rename_repo}" mv -- "${source}" "${destination}"
  jq -n --arg bound "${bound}" \
    '{allowed_paths:["src/**"],forbidden_paths:["secrets/**"],max_diff_size:$bound}' \
    >"${tmp_dir}/rename-contract.json"

  python3 "$policy" "${tmp_dir}/rename-contract.json" "${rename_repo}" \
    "${tmp_dir}/rename-policy.json" || actual_rc=$?
  if [[ "${actual_rc}" -ne "${expected_rc}" ]]; then
    echo "rename policy ${label}: expected exit ${expected_rc}, got ${actual_rc}" >&2
    exit 1
  fi
  jq -e --arg source "${source}" --arg destination "${destination}" \
    '.changed_files == ([$source, $destination] | sort) and .diff_files == 2 and .diff_lines == 0' \
    "${tmp_dir}/rename-policy.json" >/dev/null
  if [[ "${expected_rc}" -eq 0 ]]; then
    jq -e '.status == "passed" and .violations == [] and .stop_reason == null' \
      "${tmp_dir}/rename-policy.json" >/dev/null
  else
    jq -e --arg violation "${violation}" '.status == "failed" and (.violations | index($violation))' \
      "${tmp_dir}/rename-policy.json" >/dev/null
  fi
}

check_rename_policy forbidden-source secrets/key.txt src/key.txt 3 'secrets/key.txt:forbidden_path'
check_rename_policy forbidden-destination src/key.txt secrets/key.txt 3 'secrets/key.txt:forbidden_path'
check_rename_policy outside-source docs/key.txt src/key.txt 3 'docs/key.txt:outside_allowed_paths'
check_rename_policy allowed src/old.txt src/new.txt 0 ''
check_rename_policy file-bound src/old.txt src/new.txt 3 'diff:2_files_exceeds_1' '1 files'
check_rename_policy tab-source $'secrets/key\tname.txt' $'src/key\tname.txt' 3 $'secrets/key\tname.txt:forbidden_path'
check_rename_policy tab-allowed $'src/old\tname.txt' $'src/new\tname.txt' 0 ''
check_rename_policy newline-source $'docs/key\nname.txt' src/key.txt 3 $'docs/key\nname.txt:outside_allowed_paths'

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
