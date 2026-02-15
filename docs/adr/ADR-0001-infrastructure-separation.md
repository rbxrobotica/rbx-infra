# ADR-0001: Infrastructure Repository Separation

## Status

**Accepted** - 2025-01-16

## Context

RBX Systems currently manages all infrastructure and application code in a single repository (`robson`). As the platform grows to support multiple products (strategos, thalamus, websites) and multiple domains, this monolithic approach presents several challenges:

1. **Coupling**: Application code and deployment manifests are tightly coupled, making it difficult to change infrastructure without affecting application development.

2. **Governance**: Lack of centralized control over cluster-wide resources like TLS certificates, DNS records, and security policies.

3. **Duplication**: Each application repository would need to duplicate infrastructure patterns (namespaces, RBAC, ingress configurations).

4. **Portability**: Application repositories cannot be easily deployed to different environments or clusters because environment-specific configurations are embedded within them.

5. **Scalability**: As the number of products grows, managing infrastructure across multiple repositories becomes inconsistent and error-prone.

## Decision

We will create a centralized infrastructure repository (`rbx-infra`) that serves as the single source of truth for all cluster-specific configurations. This repository will implement the GitOps pattern with ArgoCD.

### Repository Responsibilities

**rbx-infra (Infrastructure Repository):**
- ArgoCD Applications and Projects
- Namespace definitions and quotas
- RBAC policies
- Deployment manifests for all environments
- TLS certificates and issuers
- DNS configurations (external-dns)
- Gateway API / Ingress configurations
- Service mesh configuration (Istio ambient)
- Platform services (cert-manager, ArgoCD)
- Bootstrap playbooks (Ansible)

**Application Repositories (robson, strategos, thalamus, websites):**
- Application source code
- Dockerfiles and build configurations
- CI/CD pipelines for building images
- Reusable Helm chart templates
- Application-specific documentation

### Architecture Pattern

```
┌─────────────────────────────────────────────────────────────────┐
│                        RBX Systems Architecture                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   Application Repos          Infrastructure Repo                │
│   ─────────────────          ──────────────────                 │
│   robson/                    rbx-infra/                         │
│   ├── apps/ (code)           ├── apps/ (deploy manifests)       │
│   ├── Dockerfile             ├── platform/ (cluster services)   │
│   ├── charts/ (templates)    ├── gitops/ (ArgoCD configs)       │
│   └── .github/ (CI build)    └── bootstrap/ (cluster setup)     │
│                                                                 │
│   strategos/                 ArgoCD syncs from rbx-infra        │
│   thalamus/                  ─────────────────────────          │
│   websites/                  Production, Staging environments   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Consequences

### Positive

1. **Clear Separation of Concerns**: Application developers focus on code; platform engineers focus on infrastructure.

2. **Centralized Governance**: All cluster changes flow through a single repository with consistent review processes.

3. **Consistency**: All applications follow the same deployment patterns, reducing cognitive load.

4. **Portability**: Application repositories become generic and can be deployed to any cluster with appropriate rbx-infra configuration.

5. **Scalability**: Adding new products requires minimal changes - just add manifests to rbx-infra.

6. **Security**: Cluster-wide security policies (RBAC, network policies) are centrally managed.

### Negative

1. **Two-Repository Workflow**: Deploying an application change requires updates to two repositories (build image → update rbx-infra).

2. **Coordination Overhead**: Changes that affect both application and infrastructure need coordinated PRs.

3. **Initial Migration Cost**: Existing infrastructure in robson needs to be migrated to rbx-infra.

### Mitigations

1. **Automated Image Updates**: Use repository_dispatch to automatically update image tags in rbx-infra when CI builds complete.

2. **Cross-Repository PRs**: Use GitHub's cross-repository PR features for coordinated changes.

3. **Migration Guide**: Comprehensive documentation for the migration process.

## Implementation

### Phase 1: Repository Creation
- Create rbx-infra repository
- Set up ArgoCD App of Apps pattern
- Migrate platform services

### Phase 2: Application Migration
- Move production manifests
- Move staging manifests
- Update ArgoCD Applications

### Phase 3: CI/CD Updates
- Split workflows between repositories
- Implement automated image tag updates

### Phase 4: Cleanup
- Remove infrastructure from robson
- Update documentation

## Related

- [GitOps Pattern](https://opengitops.dev/)
- [ArgoCD Best Practices](https://argo-cd.readthedocs.io/en/stable/user-guide/best_practices/)
- [ADR-0002: ArgoCD App of Apps Pattern](./ADR-0002-app-of-apps.md) (to be created)
