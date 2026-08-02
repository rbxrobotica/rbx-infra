#!/usr/bin/env bash
set -euo pipefail

role_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runner="${role_dir}/files/rbx-agent-runner.sh"
tasks="${role_dir}/tasks/main.yml"

bash -n "$runner"

pr_line="$(grep -n 'gh pr create' "$runner" | cut -d: -f1)"
delivered_line="$(grep -n 'report_delivered "${code}"' "$runner" | cut -d: -f1)"
if [[ -z "$pr_line" || -z "$delivered_line" || "$pr_line" -ge "$delivered_line" ]]; then
  echo "delivery contract violated: PR creation must precede report_delivered" >&2
  exit 1
fi

grep -q 'persistence_reason="git_commit_failed"' "$runner"
grep -q 'report_stop "${code}" "persistent_failure"' "$runner"
grep -q "printf 'DELIVERED input_tokens=" "$runner"
grep -q "printf 'STOP reason=" "$runner"
grep -q "RUNNER_GIT_AUTHOR_NAME='{{ runner_git_author_name }}'" "$tasks"
grep -q "RUNNER_GIT_AUTHOR_EMAIL='{{ runner_git_author_email }}'" "$tasks"
grep -Fq 'refs/remotes/origin/${base_branch}' "$runner"
grep -Fq 'worktree add --detach "${worktree}"' "$runner"
grep -Fq 'executor not started' "$runner"

# Reproduce mission-35's incident shape: an interrupted mission keeps `main`
# checked out while upstream advances. A fresh detached worktree must still be
# created from the new remote-tracking base without disturbing the stale one.
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
git init -q --bare "$tmp_dir/origin.git"
git init -q -b main "$tmp_dir/source"
git -C "$tmp_dir/source" config user.name test
git -C "$tmp_dir/source" config user.email test@example.invalid
printf 'one\n' >"$tmp_dir/source/probe.txt"
git -C "$tmp_dir/source" add probe.txt
git -C "$tmp_dir/source" commit -qm initial
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
  refs/remotes/origin/main

test "$(git -C "$tmp_dir/stale" branch --show-current)" = main
test -z "$(git -C "$tmp_dir/fresh" branch --show-current)"
test "$(git -C "$tmp_dir/fresh" rev-parse HEAD)" = \
  "$(git -C "$tmp_dir/source" rev-parse HEAD)"
grep -qx two < <(tail -1 "$tmp_dir/fresh/probe.txt")

echo "runner delivery contract: ok"
