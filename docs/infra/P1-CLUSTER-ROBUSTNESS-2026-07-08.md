# P1 Cluster Robustness Plan - 2026-07-08

Status: active planning, no production action executed by this document.

This plan starts the post-diagnostic P1 tranche for the RBX k3s cluster. It is
separate from the June 2026 P1 control-plane HA migration, which is already
complete and documented in `docs/runbooks/K3S-HA-MIGRATION.md`.

## Safety boundary

This document authorizes no mutation by itself. The following remain separate
human-gated operations: k3s upgrade, ArgoCD sync, Kubernetes patch/apply/delete,
node drain/restart, DNS change, secret rotation, image push, or data restore.

## Evidence snapshot

Collected read-only on 2026-07-08:

- Nodes: `tiger`, `altaica`, `sumatrae`, `jaguar` are `Ready`.
- Control plane: `/readyz?verbose` passes, including `etcd` and
  `etcd-readiness`.
- Version: all nodes run `v1.32.3+k3s1`.
- Metrics: `kubectl top nodes` fails; `v1beta1.metrics.k8s.io` is unavailable
  because the `metrics-server` service has no endpoints.
- GitOps: several apps are `OutOfSync`; several apps are `Synced` but not
  operationally healthy.
- Degraded apps:
  - `langfuse`: ClickHouse/ZooKeeper PVC mount failures on `local-path`; web
    CrashLoopBackOff.
  - `truthmetal`: CrashLoopBackOff; `truthmetal-tls` pending ACME challenge.
  - `rbx-ledger`: backend CrashLoopBackOff and no backend endpoint.
  - `rbx-cms`: ImagePullBackOff for non-existent `sha-89d6985` images.
  - `rbx-console`: ExternalSecret sync error.
- Storage: only `local-path` is present as StorageClass; all PVCs are node-local.
- Guardrails: ResourceQuota/LimitRange and NetworkPolicy coverage are partial;
  Pod Security Admission labels are not visible on app namespaces.

External version context verified on 2026-07-08:

- Kubernetes active release branches are `1.34`, `1.35`, and `1.36`; `1.32`
  is listed as non-active with EOL date `2026-02-28`.
- Kubernetes version skew policy keeps HA API servers within one minor version.

## P1 goals

1. Bring the cluster back inside a supported Kubernetes/K3s release window.
2. Restore cluster observability as a dependency, not a best-effort add-on.
3. Remove image-promotion paths that can produce unavailable production images.
4. Reduce GitOps drift and make live health visible in release decisions.

## Workstream A - Upgrade readiness

Owner: infra operator.

Actions:

1. Use `docs/runbooks/K3S-UPGRADE-P1.md` as the upgrade procedure.
2. Recheck K3s stable patch releases immediately before execution.
3. Upgrade sequentially by Kubernetes minor version; do not jump directly from
   `1.32` to `1.36`.
4. Before each step, verify etcd snapshots exist and at least one restore path
   has been dry-run on a non-production target.
5. During the window, upgrade one server at a time, preserving etcd quorum.

Exit criteria:

- All nodes are on a supported K3s release.
- `/readyz?verbose`, `kubectl get nodes`, ArgoCD root health, and public smoke
  checks pass after each minor step.
- Rollback target is documented for each step.

## Workstream B - Observability recovery

Owner: infra operator.

Actions:

1. Restore `metrics-server` endpoints and `kubectl top nodes`.
2. Consolidate `kube-prometheus-stack` to the `monitoring` namespace.
3. Remove or prune the duplicate `default` namespace observability stack only
   through an approved GitOps operation.
4. Add alerts for:
   - node `NotReady`;
   - metrics API unavailable;
   - empty critical Service endpoints;
   - CrashLoopBackOff and ImagePullBackOff over threshold;
   - cert-manager certificate not ready or expiring soon;
   - etcd snapshot age and snapshot sync failure;
   - ArgoCD app `Degraded` or persistent `OutOfSync`.

Exit criteria:

- Metrics API works.
- Prometheus/Grafana/Loki/Promtail have one intended owner namespace.
- Alert coverage includes empty endpoints and degraded GitOps apps.

## Workstream C - Image promotion hardening

Owner: infra + app owners.

Actions:

1. Treat direct Image Updater writes to `main` as a transition state, not the
   target production standard.
2. Move production image promotion toward branch/PR review.
3. Validate that promoted tags exist in GHCR before changing GitOps state.
4. Block `newTag: latest` in production overlays for new changes.
5. Prefer digest-pinned third-party images where practical.

Exit criteria:

- CI blocks new production `newTag: latest` changes.
- `.github/workflows/image-update.yml` opens promotion PRs instead of writing
  directly to `main`.
- `scripts/report-p1-image-debt.sh` is available for recurring backlog review.
- Promotion docs require registry existence checks and rollback target.
- Image Updater apps have a migration plan from direct-main writes to reviewed
  promotion branches.

## Workstream D - Drift and workload health closure

Owner: app owners with infra review.

Actions:

1. Triage all apps that are `Degraded`, `Progressing`, or persistent
   `OutOfSync`.
2. Document whether each issue is code, secret, image, storage, TLS, or
   declarative drift.
3. Close stale dev sandboxes; automate TTL enforcement before relying on labels.
4. Make release-readiness reports include live health, not only Git diff.

Exit criteria:

- No production app is both `Synced` and operationally broken without an
  explicit owner/action.
- Dev sandbox TTL has an enforcement path.
- Release reviews cite live ArgoCD and workload health.

## Non-goals for P1

- Replacing all storage with a replicated storage system.
- Full Pod Security Admission rollout.
- Full NetworkPolicy coverage for every namespace.
- Automated upgrades.

Those are P2/P3 after P1 restores the supported base and observability.

## Human authorization points

Each of these requires explicit authorization in the current session:

- Run any k3s upgrade command.
- Sync or rollback any ArgoCD app.
- Delete/prune duplicate observability resources.
- Repair or recreate PVC-backed state.
- Change secrets, DNS, or registry credentials.
