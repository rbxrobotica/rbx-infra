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
| `apps/testnet/` | Testnet environment manifests (exchange-connected, synthetic capital) |
| `apps/staging/` | Staging deployment manifests (shared, non-exchange) |
| `platform/` | Cluster-wide services (ArgoCD, cert-manager, Istio) |
| `core/` | Namespaces, RBAC, quotas |
| `bootstrap/` | Ansible playbooks for cluster provisioning |

### Environment tiers

Environments are birth-time properties — a deployment is testnet or production from the moment
it is defined in rbx-infra, not because of a flag toggled at runtime.

| Tier | Path | Namespace | When to use |
|------|------|-----------|-------------|
| Production | `apps/prod/{app}/` | `{app}` | Live workloads |
| Testnet | `apps/testnet/{app}/` | `{app}-testnet` | Exchange validation with synthetic capital |
| Staging | `apps/staging/` | `staging` | Shared pre-production, non-exchange services |

Currently active testnet environment: `robson-testnet` (Robson v3 on Binance testnet).
See `docs/ROBSON-TESTNET-ENVIRONMENT.md` and `docs/adr/ADR-0003-robson-testnet-isolation.md`.

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
7. **`ROBSON_BINANCE_USE_TESTNET` is forbidden in `apps/prod/`** - Its presence there is an operational incident requiring immediate removal. It may only appear in `apps/testnet/robson/robsond-config.yml`.

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
