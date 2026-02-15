# Migration Guide: robson → rbx-infra

This document describes the migration process from the monolithic `robson` repository to the separated architecture with `rbx-infra`.

## Overview

### Current State
```
robson/
├── apps/                  # Application code
├── infra/k8s/             # ALL Kubernetes manifests (mixed)
├── infra/ansible/         # Cluster provisioning
└── .github/workflows/     # CI/CD (build + deploy)
```

### Target State
```
robson/                    rbx-infra/
├── apps/ (code)           ├── apps/ (deployment manifests)
├── charts/ (templates)    ├── platform/ (cluster services)
├── Dockerfile             ├── gitops/ (ArgoCD configs)
└── .github/ (build only)  └── bootstrap/ (Ansible)
```

## Migration Phases

### Phase 1: Create rbx-infra Repository

**Status: READY**

1. Create GitHub repository `ldamasio/rbx-infra`
2. Push initial structure from this artifact
3. Configure ArgoCD to watch rbx-infra

**Files to create in rbx-infra:**

| Source | Destination |
|--------|-------------|
| `robson/infra/k8s/prod/*` | `rbx-infra/apps/prod/robson/` |
| `robson/infra/k8s/staging/*` | `rbx-infra/apps/staging/robson/` |
| `robson/infra/k8s/platform/*` | `rbx-infra/platform/` |
| `robson/infra/k8s/gitops/*` | `rbx-infra/gitops/` |
| `robson/infra/ansible/*` | `rbx-infra/bootstrap/ansible/` |
| `robson/infra/k8s/namespaces/*` | `rbx-infra/core/namespaces/` |

### Phase 2: Update ArgoCD Applications

Update all ArgoCD Application manifests to reference rbx-infra:

```yaml
# Before
source:
  repoURL: https://github.com/ldamasio/robson
  path: infra/k8s/prod

# After
source:
  repoURL: https://github.com/ldamasio/rbx-infra
  path: apps/prod/robson
```

### Phase 3: Update CI/CD Pipeline

Split the workflow in `.github/workflows/main.yml`:

**Keep in robson:**
- Build Docker images
- Push to DockerHub
- Run tests

**Move to rbx-infra:**
- Update manifest image tags
- Trigger ArgoCD sync

New workflow in robson:
```yaml
# .github/workflows/build.yml (new)
- name: Build and push images
  # ... existing build steps ...

- name: Trigger rbx-infra update
  uses: peter-evans/repository-dispatch@v2
  with:
    token: ${{ secrets.RBX_INFRA_TOKEN }}
    repository: ldamasio/rbx-infra
    event-type: image-updated
    client-payload: |
      {"image": "robson-frontend", "tag": "${{ steps.meta.outputs.tags }}"}
```

New workflow in rbx-infra:
```yaml
# .github/workflows/update-image.yml (new)
on:
  repository_dispatch:
    types: [image-updated]

jobs:
  update-manifest:
    runs-on: ubuntu-latest
    steps:
      - name: Update image tag
        run: |
          # Update kustomization.yml with new tag
```

### Phase 4: Clean Up robson

Remove from robson after migration:
- `infra/k8s/prod/`
- `infra/k8s/staging/`
- `infra/k8s/platform/`
- `infra/k8s/gitops/`
- `infra/ansible/`
- `infra/k8s/namespaces/`

Keep in robson:
- `infra/charts/robson-backend/` (reusable template)
- `infra/charts/robson-frontend/` (reusable template)
- `infra/scripts/` (development scripts)
- `infra/images/` (Dockerfiles for custom images)

## Rollback Plan

1. Revert ArgoCD Application source URLs
2. Re-sync from robson repository
3. rbx-infra becomes dormant

## Checklist

- [ ] Create rbx-infra GitHub repository
- [ ] Push initial structure
- [ ] Migrate production manifests
- [ ] Migrate staging manifests
- [ ] Migrate platform services
- [ ] Migrate ArgoCD configurations
- [ ] Update CI/CD workflows
- [ ] Verify all applications sync
- [ ] Clean up robson repository
- [ ] Update documentation

## Timeline

- **Week 1**: Create rbx-infra, migrate platform services
- **Week 2**: Migrate application manifests
- **Week 3**: Update CI/CD, verify functionality
- **Week 4**: Clean up, documentation, post-mortem
