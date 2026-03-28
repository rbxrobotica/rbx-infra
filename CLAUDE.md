# CLAUDE.md - AI Assistant Instructions

## Repository Purpose

`rbx-infra` is the central GitOps repository for RBX Systems infrastructure. All cluster-specific configurations live here.

## Architecture Pattern

```
Application Repos (robson, strategos, thalamus)  →  rbx-infra (THIS REPO)  →  ArgoCD  →  k3s Cluster
     - Source code, Dockerfiles                      - Deployment manifests
     - CI/CD build pipelines                         - ArgoCD Applications
     - Reusable Helm charts                          - Namespaces, RBAC, policies
                                                     - TLS, DNS, Gateway API
```

## Key Directories

| Directory | Purpose |
|-----------|---------|
| `gitops/app-of-apps/` | ArgoCD Applications (App of Apps pattern) |
| `gitops/projects/` | ArgoCD AppProjects (multi-tenancy) |
| `apps/prod/` | Production deployment manifests |
| `apps/staging/` | Staging deployment manifests |
| `platform/` | Cluster-wide services (ArgoCD, cert-manager, Istio) |
| `core/` | Namespaces, RBAC, quotas |
| `bootstrap/` | Ansible playbooks for cluster provisioning |

## Naming Conventions

- Files: `{resource-type}.yml` (e.g., `frontend-deploy.yml`, `backend-svc.yml`)
- Resources: `app.kubernetes.io/name` label for identification
- Namespaces: Same as application name for production

## Sync Waves

Priority order for ArgoCD sync:
1. `-10`: ArgoCD itself
2. `-5`: cert-manager, CRDs
3. `-4`: Istio, Gateway API
4. `-1`: Namespaces, AppProjects
5. `0`: Applications (default)

## Container Registry

All RBX products use **`ghcr.io/rbxrobotica/<product>`** (GitHub Container Registry).
- CI authenticates with `GITHUB_TOKEN` (automatic) — no Docker Hub credentials needed.
- See `docs/CONTAINER-REGISTRY.md` for the full standard, CI template, and migration guide.

## Important Rules

1. **No secrets in Git** - Use external-secrets or Kubernetes secrets
2. **No manual kubectl apply** - All changes through Git
3. **English only** - Official RBX Systems language
4. **K9s for operations** - Primary cluster management tool
5. **GHCR only** - No Docker Hub; all images at `ghcr.io/rbxrobotica/<product>`
6. **No ServerSideApply** - Do NOT use `ServerSideApply=true` in ArgoCD Applications (see `docs/ARGOCD-BEST-PRACTICES.md`)

## Common Tasks

### Adding a New Application

1. Create namespace in `core/namespaces/`
2. Create deployment files in `apps/prod/{app}/`
3. Create ArgoCD Application in `gitops/app-of-apps/` using this template:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-app
  namespace: argocd
spec:
  project: rbx-applications
  source:
    repoURL: https://github.com/rbxrobotica/rbx-infra
    targetRevision: main
    path: apps/prod/my-app
  destination:
    server: https://kubernetes.default.svc
    namespace: my-app
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
      # NEVER add ServerSideApply=true
```

4. Commit and push

**IMPORTANT**: See `docs/ARGOCD-BEST-PRACTICES.md` for detailed guidelines.

### Updating Image Tags

Update `apps/prod/{app}/kustomization.yml` with new tag.

## External Repositories

- [robson](https://github.com/ldamasio/robson) - Trading bot
- [strategos](https://github.com/ldamasio/strategos) - Strategy platform
- [thalamus](https://github.com/ldamasio/thalamus) - Analytics

## Domain Portfolio

| Domain | Purpose |
|--------|---------|
| `rbx.ia.br` | Main RBX Systems domain |
| `strategos.gr` | Strategos product |
| `rbxsystems.ch` | Swiss presence |
| `leandrodamasio.*` | Personal branding |
