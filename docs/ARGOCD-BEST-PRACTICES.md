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
