# ADR-0009: Agent Loop — Corbetti Runner (Phase 3)

**Status**: Proposed  
**Date**: 2026-06-13  
**Deciders**: Leandro Damasio (founder)  
**Gate**: Phase 3 of the Agent Loop Development roadmap (rbx-governance ADR-0015)

> **2026-09-22 implementation amendment (feature branch, not deployed):** the
> transport loop is split from a one-shot repository executor, provider CLIs
> sit behind normalized adapters, claim writes are fenced, repository path and
> diff policy is enforced after execution and again after verify, and terminal
> output uses the atomic Maestro result contract described below. An optional
> `source_commit` pins executable ingress to the admitted Git object. The
> original v0 routes remain as an explicit 404/405-only compatibility fallback.

## Context

Phase 2 delivered the mission lifecycle engine (Argo Workflows in `agent-missions`
namespace) and the maestro agent-loop endpoints (`/api/v1/agent-loop`). The execution
gap that remains: no real coding agent picks up a lease, runs code, and delivers
artifacts. The execution-lease Argo step registers and polls the lease, but exits only
when *something external* transitions it to `delivered` or `stopped`.

Corbetti (13.140.148.30) is the designated Agent Execution Workbench (ADR-0500). It
is already registered in the Ansible inventory under `agent_workbench`, is excluded
from `site.yml` until this baseline lands, and hosts Claude Code 2.1.177, Codex
0.139.0, and the `glm` wrapper — all installed under the devbox workspace at `~/rbx`.

The central design question: how does a Corbetti runner discover available work,
authenticate to maestro, execute safely in isolation, and deliver verifiable output?

## Decision

RBX adopts a **pull-based `rbx-agent-runner`** on Corbetti with the following
properties. This ADR covers the design; implementation is in the rollout section.

### 1. Runner protocol — pull-based lease claim

The runner polls maestro for unclaimed active leases. A new endpoint carries the
atomic pick-and-claim operation:

```
GET /api/v1/agent-loop/leases/next
Authorization: Bearer <runner-token>
X-Runner-Id: corbetti-01
```

Response (200 if claim succeeded, 204 if no work available):
```json
{
  "mission_code": "mission-2026-00007",
  "contract": { … },
  "lease_id": "uuid"
}
```

Claim is atomic in Postgres (`UPDATE … WHERE runner_id IS NULL RETURNING *`); a second
concurrent runner seeing the same lease gets 204. Poll interval: 30 s with ±5 s jitter.

The runner then heartbeats every 60 s:
```
POST /api/v1/agent-loop/missions/{code}/lease/heartbeat
```

Maestro marks a lease stale if no heartbeat for 3 minutes; another runner may then
claim it. Stale-lease reclaim uses the same `/leases/next` endpoint — no special path.

On completion:
```
POST /api/v1/agent-loop/missions/{code}/lease/state
{"state": "delivered"}   # or "stopped" with stop_reason
```

### 2. Authentication — static runner key, scoped to runner surface

Corbetti cannot use a Kubernetes projected SA token (it is outside the cluster).
A dedicated static runner key is issued, stored in:

- `pass rbx/corbetti/maestro-runner-key` on Corbetti
- Kubernetes Secret `rbx-maestro/corbetti-runner-key` (key: `token`), synced via ESO
  from `rbx-ia-br/corbetti-runner-key`

Maestro loads the key via `AGENT_LOOP_RUNNER_KEY` env var. A new auth path in
`auth.go` validates `Bearer <runner-key>` for the `/leases/next` and lease-mutating
endpoints only. The existing TokenReview path (for Argo Workflow pods) is unchanged.
The runner key grants access to no other endpoint family.

Token rotation: replace `pass` entry + k8s secret; ESO propagates; maestro picks up
on next pod restart (or live if loaded with `sync.Once` replaced by periodic reload).

### 3. Worktree isolation — one worktree per mission

```
~/rbx/worktrees/
  mission-2026-00007/        ← git worktree from repo clone
    <repo contents>
```

Each mission gets a dedicated detached git worktree created from a bare clone of
the target repo. A legacy contract starts at the current remote `base_branch`.
An executable ingress contract supplies `source_commit`; the runner proves that
the commit exists, is an ancestor of the fetched base branch, and becomes the
exact worktree `HEAD`. The worktree is the agent's entire filesystem surface.
On mission end (delivered or stopped) the runner removes the worktree;
interrupted worktrees may remain for operator inspection without reserving the
base branch needed by subsequent missions.

Bare clones live at `~/rbx/repos/<org>/<repo>.git` (already created on first use,
fetched on subsequent missions). The runner creates `worktrees/<mission-code>` with:

```bash
git -C ~/rbx/repos/rbxrobotica/rbx-infra.git fetch origin \
  '+refs/heads/main:refs/remotes/origin/main'
git -C ~/rbx/repos/rbxrobotica/rbx-infra.git worktree add --detach \
  ~/rbx/worktrees/mission-2026-00007 refs/remotes/origin/main
```

Clone, fetch, prune, worktree creation, and Git identity setup are explicit
fail-closed gates. If any setup step fails, the runner reports
`stopped/persistent_failure` and does not start the executor. This is required because
shell `errexit` is not reliable inside a function invoked from an `||` handler.

The ExecutionManifest reports the resolved worktree commit as `base_commit`.
Maestro rejects a result whose `base_commit` differs from the admitted
`source_commit`.

Only the repository named by the mission contract `repo` field is cloned.
Repository authorization is bounded by the GitHub credential; path authorization
is a separate policy enforced against the staged diff using `allowed_paths` and
`forbidden_paths`.

### 4. Agent selection

| Mission type | Primary agent | Fallback |
|---|---|---|
| `bugfix-loop` | codex | claude |
| `feature-loop` | claude | — |
| `refactor-loop` | codex | claude |
| `dependency-upgrade-loop` | codex | — |
| `review-loop` | claude | — |
| `documentation-loop` | claude | — |
| `architecture-proposal-loop` | claude | — |
| `evaluation-loop` | claude | — |

Agent invocation uses the devbox environment PATH
(`~/rbx/.devbox/nix/profile/default/bin:~/rbx/.devbox/npm-global/bin`). The runner
activates devbox env non-interactively (`devbox shellenv --init-hook`).

The agent receives a normalized prompt generated from the immutable contract:
objective, done criteria, allowed/forbidden paths and `verify_command`, plus a
reminder that publication and merge are runner/human responsibilities. The
runner does not inject context files into the worktree because those files would
themselves contaminate the staged repository diff.

The 2026-09-22 amendment treats the contract's `executor` as authoritative and
removes heuristic fallback. `rbx-executor-adapter.sh` supports `codex`,
`claude-haiku`, `claude-sonnet`, `glm` and `kimi`; an unknown value fails closed.
Each adapter normalizes provider, model, adapter version, exit code and available
token usage. The queue/heartbeat process contains no provider-specific flags.

Before verify, and again after verify (which can itself create files),
`rbx-mission-policy.py` stages and evaluates every changed path. Forbidden or
out-of-scope paths stop with `forbidden_action_attempted`; an exceeded or
unmeasurable line/file bound stops with `diff_size_exceeded`. Neither condition
can reach push or PR creation.

### 5. Artifact publication — branch + PR, then ledger

Before artifact publication, token-denominated `max_cost` is checked against the
provider CLI's terminal structured-usage event (Claude cache creation/read tokens
are included; Codex input already includes its cached subset). Provider CLIs do not
expose a trustworthy cumulative total early enough to undo spend, so this is a
post-execution gate: an over-budget mission is reported
`stopped/cost_limit_reached` and performs no verify, push, PR, or delivery.

A companion watchdog also sums structured result events over the rolling 24-hour
window. On a global cap breach it writes an operator-cleared `budget-stop` marker,
which blocks new claims immediately. If a mission is active, systemd stop is
deferred so the runner can observe the marker, report
`stopped/cost_limit_reached`, and remove its active-mission marker. The watchdog
then stops the idle runner. This preserves the fail-safe cap without stranding a
lease in `running`. Kimi remains outside token accounting until its CLI emits
structured usage.

On agent completion the runner:

1. Commits all changes on a branch `mission/<mission-code>` (or leaves the agent's
   branch if it already committed).
2. Pushes to `origin/<mission-code>` using a scoped GitHub PAT stored in
   `pass rbx/corbetti/github-pat` with `repo` scope on target repos only.
3. Opens a PR via `gh pr create` with the mission title and a link to the mission
   contract and ledger.
4. Calls `POST /api/v1/agent-loop/missions/{code}/artifacts:collect` with the PR URL,
   diff stats, and log path.
5. Transitions lease to `delivered`.

The 2026-09-22 amendment replaces steps 4–5 for v1 runners with one fenced,
idempotent `POST /missions/{code}/result`. The payload contains an
ExecutionManifest plus exactly one DeliveryManifest or FailureManifest.
Maestro atomically persists the manifests, terminal states, executor session
and ledger artifacts. A local copy is retained under
`~/rbx/manifests/<mission-code>/`. Legacy calls happen only when Maestro
explicitly answers 404/405.

No force-push, no direct push to main, no merge. Human gate (P4) is unchanged.

The runner GitHub PAT is distinct from the image-updater PAT (`rbx/github/rbx-infra-
write-pat`) and carries only the minimum scopes needed (`repo` on target repos).

### 6. Secret constraints (ADR-0500 §2)

Corbetti never holds production database credentials. The Maestro static runner
key grants access only to runner-surface endpoints; it cannot read governance
data or unrelated product records. Later ADR-0500/security-policy amendments do
place per-agent, namespace-scoped Kubernetes write kubeconfigs on Corbetti. The
repository runner clears `KUBECONFIG` plus Maestro/GitHub publication credentials
from provider child environments, but a worktree is not an OS sandbox and the
host OAuth/filesystem trust boundary remains. Container or VM isolation is a
future hardening item, not a property claimed by this PR.

Provider CLIs run from the worktree with their own OAuth/API material. The
GitHub PAT stays in the publication process and is removed from provider child
environments.

### 7. Rollout (three PRs, each manually synced)

| Step | Repo | Contents | Gate |
|---|---|---|---|
| 1 | `rbx-maestro` | `GET /leases/next` endpoint, runner-key auth mode, `runner_id` column in `mission_leases` | CI green + operator review |
| 2 | `rbx-infra` | Corbetti runner key secret (`rbx-ia-br/corbetti-runner-key` + ESO + deployment patch), Ansible `agent_workbench` role baseline (hardening, devbox PATH) | operator review |
| 3 | `rbx-infra` | Corbetti `rbx-agent-runner` shell script deployed as a systemd user service (`~/rbx/runner/rbx-agent-runner.service`) | E2E: real evaluation-loop mission delivered |

## Consequences

**Positive**
- Closes the execution gap: real coding agents handle missions end-to-end.
- Worktree isolation limits blast radius to one mission's scope.
- Pull-based design: no inbound to Corbetti, no NAT rules needed.
- Runner GitHub PAT is narrowly scoped; compromise does not touch cluster or prod data.
- Three-step rollout mirrors Phase 2 pattern; each step is independently safe to revert.

**Negative / accepted**
- Static runner key is simpler but requires rotation discipline (annual or on suspicion).
- Runner is a shell script initially (not a compiled binary); acceptable for Phase 3,
  revisit as a Go service in Phase 4 if concurrency or reliability demands it.
- Corbetti wipe loses in-progress worktrees; acceptable (ADR-0500: disposability).
- Kimi CLI is not installed (no official automated installer); missions that prefer
  Kimi fall back to claude until installed manually.

## Alternatives considered

- **Runner inside the k3s cluster**: rejected (ADR-0500 §4 — no unrestricted coding
  agents in the main cluster before Phase 8 evaluation).
- **Argo Workflows executes the agent directly**: rejected (ADR-0500 §1 — Argo is the
  lifecycle engine, not an agent brain; LLM calls are workbench responsibility).
- **SSH callback from cluster to Corbetti**: rejected (inbound exposure, violates
  pull-based constraint from ADR-0500 §2).
- **OIDC/JWT instead of static key**: better long-term but needs an OIDC provider on
  Corbetti; deferred to Phase 4 if runner is rewritten as a Go service.
