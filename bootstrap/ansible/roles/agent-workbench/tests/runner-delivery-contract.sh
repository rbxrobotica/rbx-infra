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
grep -q 'RUNNER_GIT_AUTHOR_NAME={{ runner_git_author_name }}' "$tasks"
grep -q 'RUNNER_GIT_AUTHOR_EMAIL={{ runner_git_author_email }}' "$tasks"

echo "runner delivery contract: ok"
