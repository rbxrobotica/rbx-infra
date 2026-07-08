# Runbook - P1-B Metrics and Observability Recovery

Status: draft for human-approved operational window.

This runbook covers the P1-B recovery path for cluster metrics and observability
ownership. It does not authorize a sync, patch, delete, restart, or Helm action
by itself.

## Scope

P1-B has two live findings from the 2026-07-08 read-only review:

- `metrics-server` is installed by the k3s addon manager in `kube-system`, but
  the Deployment has `spec.replicas: 0`; the `metrics-server` Service has no
  endpoints, `v1beta1.metrics.k8s.io` is `MissingEndpoints`, and `kubectl top
  nodes` fails.
- `kube-prometheus-stack` is intended to live in `monitoring`, but an older
  untracked stack also exists in `default`. Its node-exporter pods are Pending
  because port `9100` is already owned by the intended `monitoring` stack.

## Repository change

`platform/monitoring/kube-prometheus-stack.yml` keeps Prometheus Operator
admission webhooks enabled but disables the Helm `kube-webhook-certgen` patch
hook:

```yaml
prometheusOperator:
  admissionWebhooks:
    patch:
      enabled: false
```

Reason: ArgoCD reports `SyncError` because the chart's admission hook RBAC
resources already exist. The change removes repeat hook creation from the desired
manifest while preserving the webhook resources themselves.

## Metrics-server recovery gate

`metrics-server` is a k3s-managed addon, not an ArgoCD Application in
`rbx-infra`. Restoring it requires a separate operator-approved action.

Read-only precheck:

```bash
kubectl get deploy metrics-server -n kube-system -o wide
kubectl get svc,endpoints metrics-server -n kube-system
kubectl get apiservice v1beta1.metrics.k8s.io -o yaml
kubectl top nodes
```

Human decision points:

- Confirm whether `replicas: 0` was intentional.
- Confirm whether k3s config disables or overrides the metrics-server addon.
- Choose the least-invasive approved action to restore one replica.
- Record the rollback action before changing the Deployment or k3s server config.

Exit criteria:

- `metrics-server` has one Ready pod.
- `endpoints/metrics-server` has at least one address for port `https`.
- `v1beta1.metrics.k8s.io` condition `Available=True`.
- `kubectl top nodes` returns all four RBX nodes.

## Observability consolidation gate

Read-only precheck:

```bash
kubectl get applications.argoproj.io -n argocd kube-prometheus-stack -o yaml
kubectl get all -n monitoring -l app.kubernetes.io/instance=kube-prometheus-stack
kubectl get all -n default -l app.kubernetes.io/instance=kube-prometheus-stack
kubectl get pvc -n default -l app.kubernetes.io/instance=kube-prometheus-stack
```

As of 2026-07-08, no matching PVCs were found in `default`; the intended Grafana
PVC exists in `monitoring`.

Human-gated cleanup sequence:

1. Verify the `monitoring` stack is healthy enough to be the owner.
2. Save a resource inventory for the untracked `default` stack.
3. Delete only resources labeled `app.kubernetes.io/instance=kube-prometheus-stack`
   in `default`, after confirming no PVC/data retention requirement exists.
4. Reconcile `kube-prometheus-stack` in ArgoCD only after the hook conflict and
   orphaned `default` resources are understood.
5. Verify `monitoring` node-exporters remain Ready on all nodes and no duplicate
   port `9100` scheduler conflict remains.

## Rollback posture

- If disabling the Helm patch hook blocks a future chart upgrade, revert the
  Git change and handle the admission hook resources in a dedicated maintenance
  window.
- If `metrics-server` restore causes instability, roll back only the approved
  operational action taken for metrics-server; do not change ArgoCD apps as part
  of that rollback unless separately authorized.
- If cleanup of the `default` stack removes an unexpected dependency, stop and
  restore from the saved inventory or cluster backup path selected by the
  operator.

## Do not do without explicit authorization

- `kubectl scale`, `kubectl patch`, `kubectl delete`, or `kubectl rollout`.
- `argocd app sync`, `argocd app rollback`, or `argocd app set`.
- `helm upgrade`, `helm uninstall`, or direct release mutation.
- Node service restarts or k3s config edits.
