# ADR-0008: Argo Workflows as the Agent Loop mission control plane

**Status**: Proposed — this is the Phase 2 gate of the Agent Loop Development
roadmap: **nothing in this ADR is applied to the cluster before ratification.**

**Date**: 2026-06-12

**Governance anchors**:
[ADR-0015](https://github.com/rbxrobotica/rbx-governance/blob/main/docs/adr/ADR-0015-agent-loop-development.md) (Agent Loop Development),
[ADR-0500](https://github.com/rbxrobotica/rbx-governance/blob/main/docs/adr/ADR-0500-agentic-workflow-execution-boundary.md) (execution boundary),
[agent-loop-development-roadmap.md](https://github.com/rbxrobotica/rbx-governance/blob/main/docs/roadmaps/agent-loop-development-roadmap.md) (Phase 2);
rbx-maestro [ADR-0003](https://github.com/rbxrobotica/rbx-maestro/blob/main/docs/adr/ADR-0003-mission-records-residency.md) (mission records residency),
[AGENT-LOOP-PHASE1-RECONCILIATION.md](https://github.com/rbxrobotica/rbx-maestro/blob/main/docs/design/AGENT-LOOP-PHASE1-RECONCILIATION.md) (canonical mission lifecycle).

---

## Context

Phase 1 (merged, rbx-maestro PR #3) fixed the mission contract schema and the
canonical mission lifecycle: `designed → admitted → running(⇄paused) →
stopped(stop_reason) → delivered → approved|rejected → completed`. rbx-maestro
is the system of record for missions; coding agents execute **outside the main
cluster** on the workbench (Corbetti, Phase 3), pull-based, with no inbound
connections.

Something still has to drive the lifecycle mechanics — timeouts, retries,
suspend-and-wait on human gates, artifact collection, outcome recording.
ADR-0500 names Argo Workflows as the engine and draws hard fences around it:
it owns `state, dags, retries, timeouts, logs, artifacts, lifecycle` and must
not become `agent-reasoning, llm-gateway, governance-registry`.

This ADR is the technical design for that control plane inside rbx-infra. It
is design-only; manifests are authored in a follow-up PR **after** this ADR is
ratified, and applied via the normal GitOps path.

## Decision

### 1. Placement and separation from ArgoCD

| | ArgoCD (existing) | Argo Workflows (this ADR) |
|---|---|---|
| Role | GitOps CD: reconciles cluster state from this repo | Mission lifecycle engine: runs per-mission state machines |
| Namespace | `argocd` | `argo-workflows` (controller) + `agent-missions` (runs) |
| Trigger | Git commits | Mission admission in rbx-maestro |
| Writes to | Cluster (apps it manages) | rbx-maestro API + its own namespace only |

They share nothing but the cluster. Argo Workflows is itself **deployed by
ArgoCD** as a platform application (new app-of-apps entry under the
`rbx-platform` AppProject), so its installation follows the same GitOps
discipline it must never replace: the workflow engine never deploys
applications, never syncs Git, never touches ArgoCD's namespaces.

### 2. Namespaces and install mode

- `argo-workflows`: workflow-controller + argo-server (UI read-only, behind
  the existing internal access pattern; no public Ingress).
- `agent-missions`: the only namespace where `Workflow` resources run. The
  controller is installed **namespaced** (managed-namespace mode watching
  `agent-missions` only), not cluster-scope: a compromised or misbehaving
  workflow cannot schedule work anywhere else.
- Both namespaces are declared in `core/namespaces/` like every other.

### 3. Mission lifecycle WorkflowTemplates

Five WorkflowTemplates covering every state and transition of the Phase 1
machine, including the operational sub-state `paused` and the terminal
`completed` close-out. Every
step is a lifecycle operation — an HTTP call to rbx-maestro, a suspend node,
or artifact bookkeeping. **No step runs a coding agent, an LLM call, or
repository code.**

| Template | Lifecycle segment | Mechanics |
|---|---|---|
| `mission-admission` | `designed → admitted` | POST contract to maestro admission API; schema validation happens in maestro (Phase 1 schema); on `E-*` rejection the workflow ends — fail-closed, no degraded path (P12). |
| `mission-execution-lease` | `admitted → running ⇄ paused` | Registers an execution lease in maestro; the Corbetti runner **pulls** the lease (ADR-0500: no inbound to the workbench). The step polls lease state and the runner heartbeat via maestro. **`paused`** is a lease state in maestro (operator- or runner-set): while paused, the step keeps waiting but maestro stops accruing the `max_runtime` budget — the budget clock is maestro's; the workflow's own `activeDeadlineSeconds` is a hard outer bound set to `max_runtime` plus a bounded pause allowance. Missed heartbeats (while running) or budget expiry transition the run to `stopped`. |
| `mission-artifact-collection` | `running → stopped/delivered` | Registers runner-published artifacts (log, patch, test_result, summary, …) in the maestro I/O Ledger (P10). The engine stores no artifact bodies beyond Argo's own step logs. |
| `mission-gate-wait` | `delivered → approved\|rejected` | Argo `suspend` node; resumed only by a maestro callback after a human gate decision is recorded (`gate_decisions` / `approval_requests`). The engine never decides — it waits (P4). |
| `mission-outcome` | `approved\|rejected\|stopped → completed` | Records `rejected` or `stopped` + `stop_reason` (nine canonical reasons) in maestro and ends. For `approved` missions it holds one final suspend node until maestro relays the **merge event** (GitHub webhook/poll — merge itself is human, P4, outside the engine), then records the terminal **`completed`** close-out: artifacts sealed (P10) and the branch-cleanup notification emitted (P2) as a maestro event, never as a git operation by the engine. |

`risk_level: restricted` missions additionally require an `approved`
pre-admission `approval_request` before `mission-admission` will submit
(Phase 1 risk→gate mapping).

### 4. RBAC

- One ServiceAccount per concern: `workflow-controller` (namespaced controller
  permissions per upstream chart, scoped to `agent-missions`), and
  `mission-runner-sa` for workflow pods with **no Kubernetes API permissions
  beyond the Argo executor floor** — a single Role granting `create`/`patch`
  on `workflowtaskresults` (`argoproj.io`), which the executor requires to
  report step results. No core-API access of any kind; lifecycle steps talk
  to rbx-maestro over HTTP, not to the kube-apiserver. *(Precision added in
  rollout step 1 after verifying the upstream chart: the executor cannot run
  with literally zero permissions.)*
- No ClusterRole grants beyond what the namespaced controller strictly needs.
- No access of any kind to product namespaces, `argocd`, or `kube-system`.
- **No static secret is mounted into workflow pods at all.** Authentication to
  rbx-maestro uses a **projected ServiceAccount token** on `mission-runner-sa`:
  audience-bound to `rbx-maestro`, short TTL (≤ 10 minutes), rotated
  automatically by the kubelet, never stored as a `Secret` object. Maestro
  validates it via the Kubernetes TokenReview API and authorizes exactly one
  principal (`system:serviceaccount:agent-missions:mission-runner-sa`) on the
  mission-lifecycle endpoints only — admission, lease, heartbeat, artifact
  registration, gate status, outcome. Every other maestro endpoint rejects it.
  P5 is satisfied by construction: there is no production credential in the
  namespace to leak, and a stolen token is audience-bound, minutes-lived, and
  scoped to lifecycle calls that all pass maestro's own gate engine.

### 5. ResourceQuota and NetworkPolicy

- `agent-missions` ResourceQuota: hard caps on pods, CPU, memory, and
  ephemeral storage sized for lifecycle pods (they are tiny HTTP/wait steps;
  generous caps are unnecessary and unwanted).
- NetworkPolicy on `agent-missions`: default-deny both directions; egress
  allowed only to cluster DNS and the rbx-maestro Service; **no internet
  egress; no ingress.** The Corbetti runner never connects to these pods —
  all workbench coordination goes through maestro (pull model).
- `argo-workflows` namespace: controller egress to kube-apiserver and
  `agent-missions` only.

### 6. ADR-0500 fences, restated as enforcement

- **No agent reasoning in the engine** — templates contain no LLM calls; LLM
  mediation is Thalamus's (roadmap Phase 5), from the workbench side.
- **Not an LLM gateway** — NetworkPolicy gives mission pods no route to
  llm-gateway or Thalamus.
- **Not a governance registry** — workflow state is operational and
  disposable; the record of truth is rbx-maestro (its ADR-0003), approvals in
  rbx-governance. Deleting every Workflow object loses no governance data.
- **P6** — no unrestricted agents in the main cluster: enforced structurally,
  since no template runs agent code and the controller cannot schedule
  outside `agent-missions`.

## Consequences

Positive: mission timeouts/retries/gate-waits get a battle-tested engine
instead of homegrown queue code in maestro; the lifecycle is observable as
DAGs; the blast radius is two namespaces with default-deny networking.
Negative: one more platform component to operate and upgrade; Argo Workflows
CRDs enter the cluster. Risk accepted: the namespaced install and the absence
of Kubernetes API permissions on mission pods bound the failure modes to
"missions stall", never "cluster changes".

Rollout (each step its own PR, only after ratification): (1) namespaces +
AppProject wiring + controller via app-of-apps; (2) WorkflowTemplates +
RBAC + quotas + NetworkPolicies; (3) a no-op `evaluation-loop` mission
end-to-end against maestro in staging before any real mission. Note that the
root application auto-syncs `gitops/app-of-apps`, so **merging a manifest PR
is the apply** — each rollout PR must be complete and safe at merge time
(this docs-only ADR PR applies nothing). Rollback at any step: delete the
app-of-apps entry; nothing else in the cluster depends on these namespaces.

## Alternatives considered

- **Lifecycle inside rbx-maestro** (goroutines/queues): rejected — maestro is
  the registry and orchestration *plane*, not an execution engine; rebuilding
  retries, DAGs, suspend/resume, and timeout semantics in Go duplicates what
  Argo Workflows already does, and couples maestro uptime to long-running
  mission processes (contradicts its ADR-0003 fail-closed posture).
- **Argo Events + sensors**: deferred — event-driven triggering adds CRDs and
  moving parts before the first mission has run; maestro-initiated workflow
  submission is enough for Phases 2–3.
- **External runner orchestrates itself (cron on Corbetti)**: rejected — the
  workbench must stay disposable and must not hold lifecycle state
  (ADR-0500 `workbench.must_not: source-of-truth`).
