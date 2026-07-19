# Agent Guidelines

## Git remote-write policy

- Agents may inspect git status, git log, git diff, and local branch state.
- Agents may implement, test, commit, and push a feature branch, and open or update
  PRs autonomously to complete an authorized task. Authorizing the work authorizes
  its dev loop.
- Commit and push only to a short-lived feature/topic branch you created. Never
  commit to or push `main`, `master`, or `release/*`; never force-push a shared or
  protected branch.
- Agents must never merge, run `gh pr merge`, push to a protected branch, `kubectl
  apply`, `argocd app sync`, `helm install`/`upgrade`, `tofu`/`terraform apply`,
  `docker push`, `gh workflow dispatch`, change secrets/DNS/kubeconfig, or perform
  any production deploy/sync/apply/rollback unless the operator explicitly
  authorizes that exact operation in the current message.
- Authorization to commit or push a feature branch does not imply authorization to
  merge or deploy.
- Authorization from a previous session does not carry over.
- When multiple agents may be operating in the same repository, prefer separate
  worktrees or stop and ask before changing branch state.
