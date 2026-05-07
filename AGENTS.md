# Agent Guidelines

## Git remote-write policy

- Agents may inspect git status, git log, git diff, and local branch state.
- Agents may prepare local commits only when explicitly authorized by the operator.
- Agents must never run `git push`, `rtk git push`, `git push --force`, `git push --force-with-lease`, `gh pr merge`, `gh workflow dispatch`, or any other remote write operation unless the operator explicitly authorizes that exact operation in the current message.
- If the branch is ahead of origin, the agent must stop and ask for explicit push authorization.
- Authorization to commit does not imply authorization to push.
- Authorization from a previous session does not carry over.
- When multiple agents may be operating in the same repository, prefer separate worktrees or stop and ask before changing branch state.
