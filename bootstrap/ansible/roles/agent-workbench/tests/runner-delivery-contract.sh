#!/usr/bin/env bash
set -euo pipefail

role_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runner="${role_dir}/files/rbx-agent-runner-v2.sh"
executor="${role_dir}/files/rbx-mission-executor.sh"
adapter="${role_dir}/files/rbx-executor-adapter.sh"
policy="${role_dir}/files/rbx-mission-policy.py"
tasks="${role_dir}/tasks/main.yml"

bash -n "$runner"
bash -n "$executor"
bash -n "$adapter"
python3 -c 'import pathlib,sys; compile(pathlib.Path(sys.argv[1]).read_text(), sys.argv[1], "exec")' "$policy"

pr_line="$(grep -n 'gh pr create' "$executor" | cut -d: -f1)"
delivered_line="$(grep -n 'submit_delivery "${branch}"' "$executor" | cut -d: -f1)"
if [[ -z "$pr_line" || -z "$delivered_line" || "$pr_line" -ge "$delivered_line" ]]; then
  echo "delivery contract violated: PR creation must precede submit_delivery" >&2
  exit 1
fi

grep -q 'claim_token' "$runner"
grep -q 'rbx-mission-executor.sh' "$runner"
grep -Fq "printf '%s|%s\\n'" "$runner"
grep -q 'rbx-executor-adapter.sh' "$executor"
grep -q 'rbx-mission-policy.py' "$executor"
grep -q 'submit_failure path_policy' "$executor"
test "$(grep -c 'rolling budget stop requested' "$executor")" -eq 2
grep -q '/missions/${code}/result' "$executor"
if grep -q 'rm -rf' "$runner" "$executor"; then
  echo "runner must not recursively delete an unresolved path" >&2
  exit 1
fi
grep -q "RUNNER_GIT_AUTHOR_NAME='{{ runner_git_author_name }}'" "$tasks"
grep -q "RUNNER_GIT_AUTHOR_EMAIL='{{ runner_git_author_email }}'" "$tasks"
grep -q 'src: rbx-agent-runner-v2.sh' "$tasks"
grep -Fq 'refs/remotes/origin/${base_branch}' "$executor"
grep -Fq 'worktree add --detach "${worktree}"' "$executor"
grep -Fq 'merge-base --is-ancestor "${source_commit}"' "$executor"
grep -Fq '"${base_commit}" != "${source_commit}"' "$executor"

# Reproduce mission-35's incident shape: an interrupted mission keeps `main`
# checked out while upstream advances. A fresh detached worktree must still be
# created from the contract's immutable source commit without disturbing it.
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
git init -q --bare "$tmp_dir/origin.git"
git init -q -b main "$tmp_dir/source"
git -C "$tmp_dir/source" config user.name test
git -C "$tmp_dir/source" config user.email test@example.invalid
printf 'one\n' >"$tmp_dir/source/probe.txt"
git -C "$tmp_dir/source" add probe.txt
git -C "$tmp_dir/source" commit -qm initial
source_commit="$(git -C "$tmp_dir/source" rev-parse HEAD)"
git -C "$tmp_dir/source" remote add origin "$tmp_dir/origin.git"
git -C "$tmp_dir/source" push -q -u origin main
git clone -q --bare "$tmp_dir/origin.git" "$tmp_dir/cache.git"
git -C "$tmp_dir/cache.git" worktree add -q "$tmp_dir/stale" main

printf 'two\n' >>"$tmp_dir/source/probe.txt"
git -C "$tmp_dir/source" commit -qam update
git -C "$tmp_dir/source" push -q origin main
git -C "$tmp_dir/cache.git" fetch -q origin \
  '+refs/heads/main:refs/remotes/origin/main'
git -C "$tmp_dir/cache.git" worktree add -q --detach "$tmp_dir/fresh" \
  "$source_commit"

test "$(git -C "$tmp_dir/stale" branch --show-current)" = main
test -z "$(git -C "$tmp_dir/fresh" branch --show-current)"
test "$(git -C "$tmp_dir/fresh" rev-parse HEAD)" = "$source_commit"
grep -qx one < <(tail -1 "$tmp_dir/fresh/probe.txt")

echo "runner delivery contract: ok"
