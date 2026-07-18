# Runbook - P1 k3s Supported-Version Upgrade

Status: draft for human-approved maintenance window. All restore-drill and
snapshot gates validated 2026-07-18 (see "Gate status (2026-07-18)" below); only
the human-window approval remains. Approving this runbook does not execute it,
each minor step still needs a per-window go.

This runbook upgrades the RBX k3s cluster from `v1.32.3+k3s1` back into a
supported Kubernetes release line. It is intentionally written as a controlled
procedure, not an instruction to run now.

## Current state

As of 2026-07-18:

- `tiger`, `altaica`, and `sumatrae` are k3s servers with embedded etcd;
  `jaguar` is an agent and the database/analytics host (central Postgres/ParadeDB).
- All 4 nodes run `v1.32.3+k3s1` (Ready; `/readyz` ok).
- Kubernetes `1.32` is EOL (upstream since 2026-02-28); the supported stable
  line is now `1.36` (`v1.36.2+k3s1` on the k3s stable channel).

## Gate status (2026-07-18, all met)

| Gate | Status |
|---|---|
| Human-approved window + target patch | pending (this runbook requests it) |
| Latest etcd snapshot exists | OK, `etcd-snapshot-tiger-1784390402` (2026-07-18 18:00 UTC); cadence 6h, retention 20 |
| Snapshot sync to `jaguar` current | OK, lands at `/var/lib/k3s-snapshots/`; latest present |
| Restore drill performed | OK, **both** etcd and jaguar-Postgres PASS 2026-07-18 |
| Public smoke URLs listed | OK, see Post-step verification |
| Rollback target per minor | the fresh etcd snapshot saved at the end of each prior minor |
| ArgoCD degraded classified | OK, baseline in "Known pre-existing issues" |

Evidence (confidential, agnostic copies under `~/docs/`):
`~/docs/etcd-restore-drill-evidence-2026-07-18.md` and
`~/docs/jaguar-pg-restore-drill-evidence-2026-07-18.md`.

## Upgrade rule

Do not skip Kubernetes minor versions. The target sequence is:

1. `1.32` -> latest stable K3s patch for `1.33`.
2. `1.33` -> latest stable K3s patch for `1.34`.
3. Stop on `1.34` if risk is high; otherwise continue to `1.35`.
4. Continue to `1.36` only after add-on compatibility is verified.

Recheck K3s releases immediately before the window. The exact patch version is
a maintenance-window decision, not a constant in this runbook.

Concrete target candidates (2026-07-18, from the authoritative
`https://update.k3s.io/v1-release/channels`; **recheck at each window**):

| Step | From -> To |
|---|---|
| 1 | `v1.32.3+k3s1` -> `v1.33.13+k3s1` |
| 2 | `v1.33.13+k3s1` -> `v1.34.9+k3s1` |
| 3 | `v1.34.9+k3s1` -> `v1.35.6+k3s1` |
| 4 | `v1.35.6+k3s1` -> `v1.36.2+k3s1` (stable line) |

Note: the current 1.32 line is itself at `v1.32.13`, so the cluster is
patch-behind within 1.32 as well; step 1 resolves that. `1.35` is a released
line, so the no-skip path is four minor jumps.

## Pre-flight, read-only

```bash
export KUBECONFIG=~/.kube/config-rbx
kubectl get nodes -o wide
kubectl get --raw=/readyz?verbose
kubectl get applications.argoproj.io -n argocd
kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded -o wide
kubectl get pv,pvc -A -o wide
kubectl get apiservice v1beta1.metrics.k8s.io -o yaml
```

Record:

- current node versions;
- degraded ArgoCD apps;
- non-running pods;
- all PVCs and node-local `local-path` bindings;
- current etcd snapshot list and latest snapshot timestamp.

## Required gates before execution

- A human approves the exact maintenance window and target patch version.
- Latest etcd snapshot exists on the source servers.
- Snapshot sync to `jaguar` is current.
- At least one restore drill has been performed or explicitly waived.
- Public smoke-test URLs are listed.
- Rollback target for the current minor step is written down.
- ArgoCD degraded apps are classified as pre-existing or upgrade-induced.

## Critical: do NOT drain server nodes (local-path)

Every server node hosts node-bound `local-path` stateful workloads. A
`kubectl drain` evicts those pods, but their PVCs cannot rebind on another node,
leaving them `Pending` (data-loss-adjacent). The upgrade is **in-place**:

- `kubectl cordon <node>` only (prevents new scheduling; **no eviction**);
- upgrade the k3s package and restart the service in place;
- `kubectl uncordon <node>`; confirm the local-path pods returned to `Running`
  on the **same** node (not `Pending`).

local-path bindings (2026-07-18):

| Node | local-path stateful workloads |
|---|---|
| tiger | langfuse valkey (`langfuse-redis-primary-0`), langfuse zookeeper-0 |
| altaica | langfuse zookeeper-2, langfuse clickhouse, rbx-payments btcpay / bitcoind-0 / nbxplorer |
| sumatrae | monitoring grafana / prometheus / alertmanager / loki-0, langfuse zookeeper-1 |
| jaguar (agent) | none |

PDBs do not protect against a node reboot, so each restarted node causes a
~1-2 min restart blip for its single-replica workloads. This is inherent and
acceptable, and is why each minor is its own night window.

## Execution shape

Upgrade one k3s server at a time. Preserve etcd quorum (3 members -> keep 2
alive). Identify the current etcd/controller leader at window time and upgrade
it **last** among servers:

1. `kubectl cordon <non-leader server>`, upgrade k3s in place, restart service.
2. Wait for node `Ready`.
3. Check etcd member health.
4. Check `/readyz?verbose`.
5. `kubectl uncordon`; confirm local-path pods back on the same node.
6. Repeat for the next non-leader server, then the leader server last.
7. Upgrade `jaguar` agent after the servers are healthy (no local-path, no etcd).

Do not continue to the next minor version until all nodes are healthy on the
current target minor.

## Post-step verification

After each node:

```bash
kubectl get nodes -o wide
kubectl get --raw=/readyz?verbose
kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded -o wide
kubectl get applications.argoproj.io -n argocd
```

After each minor:

- Verify all intended nodes are on the same K3s minor.
- Verify ArgoCD root and platform apps.
- Verify metrics-server, Traefik, CoreDNS, cert-manager, external-secrets,
  Prometheus, Loki, and Promtail.
- Run public HTTP smoke checks.
- Save a fresh etcd snapshot.

## Rollback posture

Rollback is per minor step. Do not attempt blind downgrade across multiple minor
versions. If rollback is required:

1. Stop at the first failing node or minor step.
2. Preserve logs and current etcd snapshot metadata.
3. Restore the last known-good snapshot only under explicit human authorization.
4. Reconcile GitOps after the control plane is stable.

## Known pre-existing issues not caused by upgrade

Refreshed baseline 2026-07-18 (compare each window against this; only flag
regressions in components that were healthy here):

- ArgoCD apps `OutOfSync` / `Healthy` (cosmetic drift, known):
  `kube-prometheus-stack`, `llm-gateway`, `rbx-data`, `rbx-memory`,
  `rbx-observability`, `rbx-comms-console`.
- ArgoCD app `Degraded` (known broken ExternalSecret): `rbx-portal`.
- High-restart pods, not currently in a waiting state (do not treat as a
  regression unless they get worse): `truthmetal` (rs ~5309),
  `rbx-ledger-backend` (rs ~4920), `lda-prod` (rs ~792), `loki-0` (rs ~238).
- At the 2026-07-18 capture no pod was in a non-running/non-succeeded phase.

Do not declare the upgrade failed solely because a pre-existing issue remains.
Do declare it failed if a healthy critical dependency regresses.
