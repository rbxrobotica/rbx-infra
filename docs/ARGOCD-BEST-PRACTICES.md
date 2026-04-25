# ArgoCD Best Practices

## SyncOptions Configuration

### ❌ Avoid ServerSideApply

**DO NOT USE** `ServerSideApply=true` in ArgoCD Application syncOptions.

**Reason**: Server-Side Apply (SSA) causes field manager conflicts with:
- Helm-managed resources (Deployments, StatefulSets)
- Batch resources (CronJobs, Jobs)
- Custom Resource Definitions (CRDs)
- Webhook configurations

**Problem**: When SSA is enabled, ArgoCD and the original resource manager (e.g., Helm) compete for field ownership, causing perpetual OutOfSync status even when resources are identical.

### ✅ Recommended syncOptions

```yaml
syncOptions:
  - CreateNamespace=true           # Safe: creates namespace if missing
  - RespectIgnoreDifferences=true  # Required when using ignoreDifferences
```

### When to Use ignoreDifferences

Use `ignoreDifferences` for fields that are:
1. **Managed by controllers** (e.g., webhook caBundle, admission controller configs)
2. **Immutable after creation** (e.g., Deployment selectors, PVC volumeClaimTemplates)

**Example**:

```yaml
ignoreDifferences:
  - group: admissionregistration.k8s.io
    kind: ValidatingWebhookConfiguration
    jqPathExpressions:
      - .webhooks[].clientConfig.caBundle

  - group: apps
    kind: Deployment
    name: my-app
    jsonPointers:
      - /spec/selector
```

## Application Structure

### Sync Waves

Use sync waves for dependency ordering:

```yaml
annotations:
  argocd.argoproj.io/sync-wave: "-10"  # ArgoCD itself
  argocd.argoproj.io/sync-wave: "-5"   # CRDs, cert-manager
  argocd.argoproj.io/sync-wave: "-4"   # Service mesh, ingress
  argocd.argoproj.io/sync-wave: "-1"   # Namespaces, RBAC
  argocd.argoproj.io/sync-wave: "0"    # Applications (default)
```

### Automated Sync

Enable automated sync with prune and selfHeal:

```yaml
syncPolicy:
  automated:
    prune: true      # Delete resources not in Git
    selfHeal: true   # Revert manual cluster changes
  syncOptions:
    - CreateNamespace=true
    - RespectIgnoreDifferences=true
```

## Troubleshooting OutOfSync Issues

### 1. Check Resource Status

```bash
kubectl get application <app-name> -n argocd -o json | \
  jq '.status.resources[] | select(.status == "OutOfSync")'
```

### 2. Force Hard Refresh

```bash
kubectl annotate application <app-name> -n argocd \
  argocd.argoproj.io/refresh=hard --overwrite
```

### 3. Check Field Manager Conflicts

```bash
kubectl get <resource> <name> -n <namespace> -o yaml | \
  yq eval '.metadata.managedFields'
```

Look for multiple managers (e.g., `helm`, `kubectl`, `argocd-controller`).

### 4. Remove ServerSideApply

If you see perpetual OutOfSync:
1. Remove `ServerSideApply=true` from the Application
2. Commit and push to Git
3. Wait for parent Application to sync (e.g., `root`, `platform`)
4. Hard refresh the affected Application

## Common Patterns

### Helm Chart Application

```yaml
source:
  repoURL: https://charts.example.com
  chart: my-chart
  targetRevision: v1.0.0
  helm:
    values: |
      key: value

syncPolicy:
  automated:
    prune: true
    selfHeal: true
  syncOptions:
    - CreateNamespace=true
    - RespectIgnoreDifferences=true

ignoreDifferences:
  - group: apps
    kind: Deployment
    jsonPointers:
      - /spec/selector
```

### Directory-Based Application

```yaml
source:
  repoURL: https://github.com/org/repo
  targetRevision: main
  path: apps/prod/my-app

syncPolicy:
  automated:
    prune: true
    selfHeal: true
  syncOptions:
    - CreateNamespace=true
```

## References

- [ArgoCD Sync Options](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-options/)
- [Server-Side Apply](https://kubernetes.io/docs/reference/using-api/server-side-apply/)
- [Ignore Differences](https://argo-cd.readthedocs.io/en/stable/user-guide/diffing/)

---

## Accessing the ArgoCD UI

URL: <https://argocd.rbx.ia.br>

User: `admin`

Initial password retrieval (only valid until rotation):

```bash
KUBECONFIG=~/.kube/config-rbx \
  kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d; echo
```

After first login the operator rotates the password and deletes
`argocd-initial-admin-secret`. The rotated password lives in
`pass`; ask the operator for the entry path.

The UI is for **observing** state. All mutations go through git
via ArgoCD GitOps. Do not use the UI to edit, sync-overrides, or
force-create resources unless you are responding to an incident
under operator direction.

---

## Debugging an Application that won't sync

When you see `OutOfSync` or `Degraded`:

```bash
KUBECONFIG=~/.kube/config-rbx kubectl get app -n argocd
KUBECONFIG=~/.kube/config-rbx kubectl describe app -n argocd <app-name>
```

Look for `conditions[*].message` and the `status.operationState`
trace. The most common causes:

- **CRD not yet installed.** Sync wave `-5` (CRDs) must complete
  before `0` (apps). Check `kube-system` for missing CRDs.
- **Resource conflict from a previous manual `kubectl apply`.**
  Resource has an `app.kubernetes.io/managed-by: ...` annotation
  that ArgoCD does not own. Resolution: remove the conflicting
  annotation manually (one-time, document in incident log), then
  refresh. See `docs/INCIDENT-2026-03-28-ARGOCD-OUTOFSYNC.md`.
- **Image pull failure.** Pod stays `ImagePullBackOff`. Check
  whether the image tag exists in GHCR and whether the package
  is public (or has the right `imagePullSecret`).
- **Health hook failing.** Some Applications include health
  checks; describe the relevant Pod for hints.

`syncPolicy.automated.selfHeal: true` will keep retrying. If you
need to **stop** a retry loop temporarily (because something is
genuinely wrong), set the Application to manual:

```bash
argocd app set <app-name> --sync-policy none
# fix the underlying problem
argocd app set <app-name> --sync-policy automated --auto-prune --self-heal
```

Restore automatic sync as soon as the problem is resolved. Don't
leave an Application on manual mode silently.
