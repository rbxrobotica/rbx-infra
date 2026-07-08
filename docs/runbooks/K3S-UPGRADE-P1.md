# Runbook - P1 k3s Supported-Version Upgrade

Status: draft for human-approved maintenance window.

This runbook upgrades the RBX k3s cluster from `v1.32.3+k3s1` back into a
supported Kubernetes release line. It is intentionally written as a controlled
procedure, not an instruction to run now.

## Current state

As of 2026-07-08:

- `tiger`, `altaica`, and `sumatrae` are k3s servers with embedded etcd.
- `jaguar` is an agent and database/analytics host.
- All nodes run `v1.32.3+k3s1`.
- Kubernetes `1.32` is EOL.
- Active upstream Kubernetes branches are `1.34`, `1.35`, and `1.36`.

## Upgrade rule

Do not skip Kubernetes minor versions. The target sequence is:

1. `1.32` -> latest stable K3s patch for `1.33`.
2. `1.33` -> latest stable K3s patch for `1.34`.
3. Stop on `1.34` if risk is high; otherwise continue to `1.35`.
4. Continue to `1.36` only after add-on compatibility is verified.

Recheck K3s releases immediately before the window. The exact patch version is
a maintenance-window decision, not a constant in this runbook.

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

## Execution shape

Upgrade one k3s server at a time. Preserve etcd quorum:

1. Upgrade a non-initial server.
2. Wait for node `Ready`.
3. Check etcd member health.
4. Check `/readyz?verbose`.
5. Repeat for the next server.
6. Upgrade `jaguar` agent after the servers are healthy.

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

As of 2026-07-08:

- `metrics-server` unavailable.
- duplicate/default observability stack has pending node-exporters.
- `langfuse` degraded by `local-path` PVC mount failures.
- `truthmetal` CrashLoopBackOff and TLS challenge pending.
- `rbx-ledger-backend` CrashLoopBackOff.
- `rbx-cms` ImagePullBackOff for non-existent image tag.
- `rbx-console-users-access` ExternalSecret sync error.

Do not declare the upgrade failed solely because a pre-existing issue remains.
Do declare it failed if a healthy critical dependency regresses.
