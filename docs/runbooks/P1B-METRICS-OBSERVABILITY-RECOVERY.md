# Runbook - P1-B Metrics and Observability Recovery

Status: active recovery notes and GitOps guardrails; operational actions remain human-gated.

This runbook covers the P1-B recovery path for cluster metrics and observability
ownership. It does not authorize a sync, patch, delete, restart, or Helm action
by itself.

## Scope

P1-B started with two live findings from the 2026-07-08 read-only review:

- `metrics-server` was installed by the k3s addon manager in `kube-system`, but
  the Deployment had `spec.replicas: 0`; the `metrics-server` Service had no
  endpoints, `v1beta1.metrics.k8s.io` was `MissingEndpoints`, and `kubectl top
  nodes` failed.
- `kube-prometheus-stack` was intended to live in `monitoring`, but an older
  untracked stack also existed in `default`. Its node-exporter pods were Pending
  because port `9100` was already owned by the intended `monitoring` stack.

As of 2026-07-10, both recovery gates are closed by read-only verification:

- `metrics-server` is `1/1` Ready, its Service has a ready endpoint,
  `v1beta1.metrics.k8s.io` is `Available=True`, and `kubectl top nodes` returns
  all four RBX nodes.
- `kube-prometheus-stack` is `Synced/Healthy` in `monitoring`; the duplicate
  `default` namespace resources and PVCs for `app.kubernetes.io/instance=kube-prometheus-stack`
  are absent.
- Prometheus, Grafana, Alertmanager, kube-state-metrics, node-exporter, and
  Promtail are running under the intended `monitoring` owner. Loki is running but
  remains a watch item because `loki-0` has a high historical restart count.
- Prometheus currently sees `CrashLoopBackOff` signals for `truthmetal` and
  `rbx-ledger`, which remain Workstream D application-health follow-ups.

## Repository change

`platform/monitoring/kube-prometheus-stack.yml` temporarily disables Prometheus
Operator admission webhooks and removes `Replace=true` from sync options:

```yaml
prometheusOperator:
  admissionWebhooks:
    enabled: false
```

Reason: after the first P1-B merge, ArgoCD no longer failed on repeated
`kube-webhook-certgen` hook RBAC creation, but it still failed because:

- `Replace=true` attempted to replace the bound Grafana PVC, which Kubernetes
  forbids because most PVC spec fields are immutable after binding.
- The existing `prometheusrulemutate` webhook had an invalid CA/certificate and
  rejected PrometheusRule reconciliation.

Disabling admission webhooks is a temporary unblock so the chart can reconcile.
Regenerating webhook certificates is a separate maintenance-window action.

`ServerSideApply=true` is enabled for this Application because the chart renders
large Prometheus Operator CRDs. Client-side apply stores previous desired state in
`kubectl.kubernetes.io/last-applied-configuration`; after the P1-B alert merge,
ArgoCD retried CRD patches that exceeded Kubernetes' annotation size limit.
Server-side apply keeps the CRDs in desired state without depending on that large
annotation.

Do not set `crds.enabled: false` as a routine workaround while automated prune is
enabled. The CRDs are already tracked by the Application; removing them from the
rendered desired state could make them prune candidates. If CRD ownership changes,
handle it as a dedicated maintenance-window migration with explicit backup and
rollback criteria.

## Initial alert coverage

`platform/monitoring/kube-prometheus-stack.yml` adds the first P1-B
`additionalPrometheusRulesMap` group for metrics that are already present in the
current Prometheus scrape set:

- `RBXMetricsServerEndpointMissing` fires when the `metrics-server` endpoint has
  no ready address.
- `RBXPodCrashLoopOrImagePull` fires when any pod container remains in
  `CrashLoopBackOff`, `ImagePullBackOff`, or `ErrImagePull`.
- `RBXCriticalEndpointNotReady` fires when the critical `langfuse`, `rbx-ledger`,
  or `truthmetal` endpoint set has no ready backend address.

The following Workstream B alerts remain pending until their metric sources are
scraped or exposed: ArgoCD app health, cert-manager certificate readiness or
expiry, APIService condition status, and etcd snapshot age or sync failure.

## P1-C Prometheus OOM recovery

After PR #94 merged on 2026-07-09, `root` and `platform` reconciled the merge
commit and `kube-prometheus-stack` became `Synced`. The remaining degraded state
moved to the Prometheus custom resource: pod
`prometheus-kube-prometheus-stack-prometheus-0` was `CrashLoopBackOff` because
the `prometheus` container was repeatedly `OOMKilled` while replaying WAL and
running TSDB compaction/checkpoint work.

Declarative remediation:

- Increase Prometheus memory request from `256Mi` to `512Mi`.
- Increase Prometheus memory limit from `512Mi` to `1536Mi`.
- Reduce `retentionSize` from `9GB` to `6GB` on the existing 10Gi PVC, leaving
  head, WAL, and compaction overhead outside the retention target.

Read-only exit criteria after ArgoCD reconciliation:

- `kube-prometheus-stack` is `Synced/Healthy`.
- `prometheus-kube-prometheus-stack-prometheus-0` is `2/2 Running`.
- The `prometheus` container has no new `OOMKilled` restart after the rollout.
- Prometheus service and Grafana service remain `Healthy` in ArgoCD.

Rollback posture:

- Revert the Git resource/retention change if the new limit creates node pressure
  or scheduling failures.
- Do not delete the Prometheus PVC or manually compact TSDB data outside an
  approved maintenance window.

## Metrics-server recovery gate

`metrics-server` is a k3s-managed addon, not an ArgoCD Application in
`rbx-infra`. Its 2026-07-10 recovery is verified; any future mutation to restore
or roll back it still requires a separate operator-approved action.

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

As of 2026-07-10, no matching resources or PVCs were found in `default`; the
intended Grafana PVC exists in `monitoring`.

Human-gated cleanup sequence if the duplicate `default` stack reappears:

1. Verify the `monitoring` stack is healthy enough to be the owner.
2. Save a resource inventory for the untracked `default` stack.
3. Delete only resources labeled `app.kubernetes.io/instance=kube-prometheus-stack`
   in `default`, after confirming no PVC/data retention requirement exists.
4. Reconcile `kube-prometheus-stack` in ArgoCD only after the hook conflict and
   orphaned `default` resources are understood.
5. Verify `monitoring` node-exporters remain Ready on all nodes and no duplicate
   port `9100` scheduler conflict remains.

## Rollback posture

- If disabling admission webhooks blocks a future chart upgrade, revert the Git
  change only after the webhook certificate/CA bundle has been regenerated and
  verified.
- If removing `Replace=true` leaves an intended immutable-resource change
  unapplied, handle that resource explicitly in a dedicated maintenance window;
  do not re-enable broad replace for the whole Application by default.
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
