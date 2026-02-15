# RBX Infrastructure

Central GitOps repository for RBX Systems Kubernetes cluster infrastructure.

## Overview

This repository serves as the **single source of truth** for all cluster-specific infrastructure, following the GitOps pattern with ArgoCD. It implements a clear separation of concerns between application code and infrastructure.

### Architecture Pattern

```
┌─────────────────────────────────────────────────────────────────┐
│                        RBX Systems Architecture                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   Application Repos          Infrastructure Repo                │
│   ─────────────────          ──────────────────                 │
│   robson/                    rbx-infra/ (THIS REPO)             │
│   ├── apps/ (code)           ├── apps/ (deploy manifests)       │
│   ├── Dockerfile             ├── platform/ (cluster services)   │
│   ├── charts/ (templates)    ├── gitops/ (ArgoCD configs)       │
│   └── .github/ (CI build)    └── bootstrap/ (cluster setup)     │
│                                                                 │
│   strategos/                 ArgoCD syncs from rbx-infra        │
│   thalamus/                  ─────────────────────────          │
│   websites/                  Production, Staging, Preview envs  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Repository Structure

```
rbx-infra/
├── apps/                    # Application deployments
│   ├── prod/               # Production manifests
│   │   ├── robson/         # Robson application
│   │   ├── strategos/      # Strategos application
│   │   ├── thalamus/       # Thalamus application
│   │   └── websites/       # Static websites
│   └── staging/            # Staging environment
│       └── robson/
├── platform/                # Cluster-wide services
│   ├── argocd/             # GitOps controller
│   ├── cert-manager/       # TLS certificate management
│   ├── external-dns/       # DNS automation
│   ├── gateway-api/        # Gateway API CRDs
│   └── istio-ambient/      # Service mesh
├── gitops/                  # ArgoCD configuration
│   ├── app-of-apps/        # Root Application + children
│   ├── projects/           # ArgoCD AppProjects
│   └── applicationsets/    # Application generators
├── core/                    # Cluster resources
│   ├── namespaces/         # Namespace definitions
│   ├── rbac/               # Role-based access control
│   └── quotas/             # Resource quotas
├── bootstrap/               # Cluster bootstrap
│   └── ansible/            # Infrastructure provisioning
└── docs/                    # Documentation
    └── adr/                # Architecture Decision Records
```

## Principles

### 1. Separation of Concerns
- **Application repositories** contain: source code, Dockerfiles, Helm chart templates, CI build pipelines
- **rbx-infra** contains: deployment manifests, environment configs, secrets references, cluster policies

### 2. GitOps Native
- All changes flow through Git commits and PRs
- ArgoCD synchronizes cluster state from this repository
- No manual `kubectl apply` in production

### 3. Environment Parity
- Production and staging use the same patterns
- Differences are expressed through Kustomize overlays or Helm values

### 4. Domain Centralization
- All domain routing (Ingress/Gateway API) defined here
- TLS certificates managed centrally
- DNS automation via external-dns

## Environments

| Environment | Namespace Pattern | Domain Pattern | Purpose |
|-------------|-------------------|----------------|---------|
| Production | `{app}` | `*.rbx.ia.br` | Live services |
| Staging | `staging` | `staging.*.rbx.ia.br` | Pre-production testing |

## Managed Applications

### Product Applications
| Application | Repository | Domains |
|-------------|------------|---------|
| Robson | `ldamasio/robson` | `app.robson.rbx.ia.br`, `api.robson.rbx.ia.br` |
| Strategos | `ldamasio/strategos` | `strategos.gr`, `strategos.rbx.ia.br` |
| Thalamus | `ldamasio/thalamus` | `thalamus.rbx.ia.br` |
| Websites | `ldamasio/websites` | `rbxsystems.ch`, `rbx.ia.br` |

### Platform Services
| Service | Purpose | Status |
|---------|---------|--------|
| ArgoCD | GitOps controller | Active |
| cert-manager | TLS automation | Active |
| external-dns | DNS automation | Planned |
| Istio Ambient | Service mesh | Active |
| Gateway API | Ingress replacement | Active |

## Quick Start

### Prerequisites
- k3s cluster with kubeconfig access
- ArgoCD installed and configured

### Bootstrap New Cluster

```bash
# 1. Install ArgoCD
kubectl apply -k platform/argocd

# 2. Apply the root Application (App of Apps)
kubectl apply -f gitops/app-of-apps/root.yml

# 3. Verify sync
argocd app list
```

### Adding a New Application

1. Create namespace in `core/namespaces/`
2. Create deployment manifests in `apps/prod/{app}/`
3. Create ArgoCD Application in `gitops/app-of-apps/`
4. Commit and push - ArgoCD will sync automatically

## RBAC and Access

| Role | Access Level | Managed By |
|------|--------------|------------|
| Platform Admin | Full cluster access | Ansible bootstrap |
| Developer | Namespace-scoped | ArgoCD Projects |

## Related Repositories

- [robson](https://github.com/ldamasio/robson) - Trading bot application
- [strategos](https://github.com/ldamasio/strategos) - Strategy platform
- [thalamus](https://github.com/ldamasio/thalamus) - Analytics service

## License

Proprietary - RBX Systems
