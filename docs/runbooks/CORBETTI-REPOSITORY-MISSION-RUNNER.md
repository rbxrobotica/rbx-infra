# Corbetti repository mission runner v1

This runbook covers the provider-neutral, repository-bound runner introduced by
the 2026-09-22 ADR-0009 amendment. The repository state is implementation-only;
do not apply the role until the matching Maestro PR and migration are deployed.

## Component boundaries

| Runtime file | Responsibility |
|---|---|
| `rbx-agent-runner.sh` | HTTPS claim polling, fenced heartbeat, process supervision |
| `rbx-mission-executor.sh` | one mission's worktree → policy → verify → Git/PR → result pipeline |
| `rbx-executor-adapter.sh` | Claude, Codex, GLM and Kimi CLI invocation plus normalized usage |
| `rbx-mission-policy.py` | staged-diff path glob and `max_diff_size` enforcement |

Ansible deploys `rbx-agent-runner-v2.sh` as the runtime
`~/rbx/runner/rbx-agent-runner.sh`. The old repository file remains only as a
rollback/reference implementation and is not copied by the role.

Provider child processes have the Maestro and GitHub publication credentials
removed from their environment. Worktrees are repository-isolation boundaries,
not OS sandboxes: the existing Corbetti host/OAuth trust model and security
policy still apply. Container or VM isolation remains a future hardening item.

## Preconditions

1. Maestro migration `009_runner_result_manifests` is applied and
   `/api/v1/agent-loop/missions/{code}/result` is reachable.
2. Corbetti's existing runner key and scoped GitHub credential are valid.
3. `jq`, `python3`, Git, `gh`, and the selected provider CLI are present.
4. No production deploy, merge or direct protected-branch push is authorized.

## Repository-only verification

Run from `rbx-infra`:

```bash
bash bootstrap/ansible/roles/agent-workbench/tests/runner-delivery-contract.sh
bash bootstrap/ansible/roles/agent-workbench/tests/runner-provider-neutral-contract.sh
bash bootstrap/ansible/roles/agent-workbench/tests/runner-e2e-contract.sh
bash bootstrap/ansible/roles/agent-workbench/tests/watchdog-budget-contract.sh
```

The E2E test uses a temporary local bare Git remote and fake Codex, GitHub and
Maestro commands. It proves both delivery and forbidden-path failure without
network access or production state.

## Staged rollout (operator action, not performed by this change)

1. Deploy the reviewed Maestro PR and verify migration 009.
2. On an operator workstation, run Ansible check mode limited to Corbetti.
3. Review the diff. Apply the role only with explicit production authorization.
4. Admit one low-risk canary with a small `allowed_paths`, `max_diff_size`,
   `max_runtime`, token bound and deterministic `verify_command`.
5. Inspect:

   - `systemctl --user status rbx-agent-runner`;
   - `journalctl --user -u rbx-agent-runner -n 200`;
   - `~/rbx/manifests/<mission-code>/{execution,delivery|failure,result}.json`;
   - the Mission detail and I/O Ledger in Maestro;
   - the pushed `mission/<mission-code>` branch and open PR.

6. Do not merge the canary until human review and required GitHub checks pass.

## Failure semantics

- Result POST 200/201: acknowledged; worktree is removed.
- Result POST 404/405: pre-v1 Maestro; legacy terminal route is attempted.
- Result POST 400/409/5xx or network failure: no downgrade. The worktree is
  preserved and the fenced lease is allowed to retry/reclaim.
- Path violation: no verify, push or PR; FailureManifest uses
  `forbidden_action_attempted`.
- Diff bound violation: no verify, push or PR; FailureManifest uses
  `diff_size_exceeded`.
- Verify failure: no push or PR; FailureManifest records command and exit code.
- Git/PR failure: branch/worktree evidence is preserved as applicable and the
  Mission stops with `persistent_failure`.

## Rollback

Revert the Ansible role commit and apply the previous role only under explicit
authorization. Maestro's additive migration and legacy endpoints can remain;
do not roll the schema back while `mission_execution_results` contains audit
records. Never delete mission branches, PRs, manifests or ledger rows as part
of an automated rollback.
