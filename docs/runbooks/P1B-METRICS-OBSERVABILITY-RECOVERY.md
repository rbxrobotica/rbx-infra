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
- `RBXCriticalEndpointNotReady` fires when a `langfuse`, `rbx-ledger`,
  `truthmetal` or `websites` endpoint set has no ready backend address.
  `websites` holds the public brand sites.

### Public brand sites (`websites`)

The rules for the brand sites read an explicit inventory in
`platform/monitoring/kube-prometheus-stack.yml`: one Endpoints name and one
Certificate name per site, added in the change that deploys the site. Today the
inventory has Initelo (`initelo`, `initelo-tls`).

- `RBXWebsiteEndpointMissing`: kube-state-metrics exports no Endpoints object
  for a registered site for 15 minutes.
- `RBXWebsiteCertificateNotReady`: a Certificate of the namespace is not Ready
  for 30 minutes.
- `RBXWebsiteCertificateExpiringSoon`: a Certificate of the namespace has less
  than 14 days left, or has expired, for 1 hour. A Certificate that was never
  issued is left to the alert above.
- `RBXWebsiteCertificateMetricsAbsent`: a registered Certificate has no
  readiness or no expiration series for 15 minutes.

The certificate series are intended to come from the cert-manager controller,
with `prometheus.enabled: true` in `platform/cert-manager/application.yml` and
the `kubernetes-pods-annotations` scrape job. That is configuration, not proof
of collection. After any change to it, verify in Prometheus that
`up{job="kubernetes-pods-annotations", kubernetes_namespace="cert-manager"}` is
1, that `certmanager_certificate_ready_status{namespace="websites"}` returns
each registered Certificate with the `namespace`, `name`, `condition` and
issuer labels, and that the four alerts are loaded without evaluation errors.

What these alerts do not cover: a Certificate or Service that is not in the
inventory; whether cert-manager's own view of the cluster is current; the
public path (DNS, ingress, the certificate actually served); and delivery of
the notification. A probe of the public address is still missing.

Operating notes:

- The readiness and expiry alerts identify a Certificate by namespace and name
  and take the worst of the latest samples of its series over the last 20
  minutes, so a controller restart does not restart their clock. More than 20
  minutes without samples does; for a registered Certificate
  `RBXWebsiteCertificateMetricsAbsent` fires no later than that evaluation.
  Firing is not delivery of the notification.
- Because the worst recent sample wins, a recovery first reported by a new
  controller pod takes up to 20 minutes to clear the alert, and can let a
  pending alert fire in that time.
- After changing a Certificate's `issuerRef`, restart the cert-manager
  controller: v1.14 keeps exporting the gauges of the previous issuer until
  then, which the 20 minute window does not bound, and the alerts take the
  worst value they see.
- A new site whose host names do not resolve to the ingress yet cannot complete
  HTTP-01, so `RBXWebsiteCertificateNotReady` fires for it 30 minutes after it
  is deployed. That alert is not waived by this explanation. A rollout that
  accepts it needs, in its own pull request, the name of the pending
  Certificate, the reason, and a dated DNS cutover that bounds it; an alert on
  any other Certificate, or one that outlives the cutover, is investigated with
  `docs/runbooks/CERT-MANAGER-DEBUG.md`.

Releasing a change to these rules or to the cert-manager metrics setting.
A check that fails leaves the release unaccepted until it is explained.

1. Before the merge, record the baseline:
   - the cert-manager pods and the Prometheus pod are Running and Ready, with
     no recent restarts;
   - Prometheus memory against its 2Gi limit;
   - `kubectl get certificate -n websites` and
     `kubectl get endpoints -n websites` show every registered name, Ready and
     with addresses;
   - current evidence that the `email-ops` route delivers: a recent alert
     notification received, or a controlled test of the route.
   An unexplained failure there is resolved first.
2. After the sync, within 15 minutes:
   - the cert-manager controller pod is Running and Ready with the three
     `prometheus.io/*` annotations;
   - `up{job="kubernetes-pods-annotations", kubernetes_namespace="cert-manager"}`
     is 1;
   - for each registered Certificate,
     `certmanager_certificate_ready_status{namespace="websites"}` returns it
     with its `namespace`, `name`, `condition` and issuer labels, the
     `condition="True"` series at 1, and
     `certmanager_certificate_expiration_timestamp_seconds` is positive and
     matches the certificate's real expiry;
   - for each registered site, `kube_endpoint_info{namespace="websites"}` and
     `kube_endpoint_address{namespace="websites", ready="true"}` return its
     Endpoints object and backends;
   - the five rules this section depends on (`RBXCriticalEndpointNotReady`,
     `RBXWebsiteEndpointMissing` and the three `RBXWebsiteCertificate*`) are
     loaded without evaluation errors and inactive, apart from one explained
     in advance by the pull request.
3. Observe for 60 minutes: no restart of cert-manager or Prometheus, Prometheus
   memory not climbing towards its limit, none of those alerts firing.
4. Roll back, by reverting the pull request, if the controller is not Ready 10
   minutes after the sync, if Prometheus restarts or exceeds 85% of its memory
   limit, or if `RBXWebsiteCertificateMetricsAbsent` fires and the scrape
   cannot be fixed within the hour. The revert removes this coverage again.

The following Workstream B alerts remain pending until their metric sources are
scraped or exposed: ArgoCD app health, cert-manager certificate readiness or
expiry outside the `websites` namespace, APIService condition status, and etcd
snapshot age or sync failure.

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
