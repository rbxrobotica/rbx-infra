# Robson Repository Refactor Guide

This document describes how to refactor the `robson` repository after the migration to `rbx-infra`.

## Files to Remove from Robson

After the migration is complete and verified, remove the following directories and files:

### Directories to Delete

```bash
# Kubernetes production manifests (moved to rbx-infra/apps/prod/robson/)
rm -rf infra/k8s/prod/

# Kubernetes staging manifests (moved to rbx-infra/apps/staging/robson/)
rm -rf infra/k8s/staging/

# Platform services (moved to rbx-infra/platform/)
rm -rf infra/k8s/platform/

# GitOps configurations (moved to rbx-infra/gitops/)
rm -rf infra/k8s/gitops/

# Datalake manifests (moved to rbx-infra/apps/datalake/)
rm -rf infra/k8s/datalake/

# Namespace definitions (moved to rbx-infra/core/namespaces/)
rm -rf infra/k8s/namespaces/

# Ansible playbooks (moved to rbx-infra/bootstrap/ansible/)
rm -rf infra/ansible/

# DNS infrastructure (moved to rbx-infra/core/dns/)
rm -rf infra/apps/dns/
```

### Files to Keep in Robson

Keep the following directories - they contain reusable templates and development tools:

```bash
# Helm chart templates (reusable)
infra/charts/robson-backend/   # Keep
infra/charts/robson-frontend/  # Keep

# Docker images (build configurations)
infra/images/                  # Keep

# Development scripts
infra/scripts/                 # Keep (except k9s-preview.sh)

# Documentation
infra/docs/                    # Keep
docs/                          # Keep
```

## GitHub Workflow Updates

### Split the CI/CD Pipeline

The current `.github/workflows/main.yml` handles both building and deploying. Split it:

#### New: `.github/workflows/build.yml` (keep in robson)

```yaml
name: Build and Test

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  build-frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Login to DockerHub
        uses: docker/login-action@v3
        with:
          username: ${{ secrets.DOCKERHUB_USERNAME }}
          password: ${{ secrets.DOCKERHUB_TOKEN }}

      - name: Build and push frontend
        uses: docker/build-push-action@v5
        with:
          context: ./apps/frontend
          push: true
          tags: |
            ldamasio/robson-frontend:latest
            ldamasio/robson-frontend:${{ github.sha }}

      - name: Notify rbx-infra
        if: github.ref == 'refs/heads/main'
        uses: peter-evans/repository-dispatch@v2
        with:
          token: ${{ secrets.RBX_INFRA_TOKEN }}
          repository: ldamasio/rbx-infra
          event-type: image-updated
          client-payload: |
            {"app": "robson", "image": "robson-frontend", "tag": "${{ github.sha }}"}

  build-backend:
    # Similar structure for backend
```

### Remove from Workflow

Remove these steps from the robson workflow:

```yaml
# REMOVE - these should happen in rbx-infra
- name: Update K8s manifests
  run: sed -i ...

- name: Commit manifest changes
  run: git commit ...
```

## Update Documentation

### Update README.md

Remove infrastructure-specific sections:

- Remove GitOps deployment instructions (now in rbx-infra)
- Remove cluster-specific information
- Keep build and development instructions
- Add link to rbx-infra for deployment info

### Update CLAUDE.md

Simplify to focus on application development:

```markdown
# CLAUDE.md

## Repository Purpose

Robson is a cryptocurrency trading bot. This repository contains:
- Application code (Django backend, React frontend, Rust daemon)
- Docker build configurations
- Reusable Helm chart templates

## Infrastructure

All deployment and cluster configurations are in [rbx-infra](https://github.com/ldamasio/rbx-infra).

## Development

### Build
\`\`\`bash
# Build Docker images
docker build -t robson-frontend ./apps/frontend
docker build -t robson-backend ./apps/backend/monolith
\`\`\`

### Test
\`\`\`bash
# Run tests
./scripts/test.sh
\`\`\`
```

## Verification Checklist

Before deleting files from robson:

- [ ] rbx-infra repository is created and accessible
- [ ] ArgoCD Applications point to rbx-infra
- [ ] Production deployment is working from rbx-infra
- [ ] CI/CD pipeline builds and pushes images correctly
- [ ] Image tag updates trigger rbx-infra updates
- [ ] All documentation is updated
- [ ] Team is informed of the change

## Rollback Plan

If issues arise after the refactor:

1. Revert ArgoCD Applications to point to robson
2. Restore deleted files from git history
3. Revert workflow changes

```bash
# Restore deleted files
git checkout HEAD~1 -- infra/k8s/prod/
git checkout HEAD~1 -- infra/k8s/gitops/
# etc.
```

## Timeline

- **Day 1**: Create rbx-infra, verify ArgoCD sync
- **Day 2**: Update CI/CD workflows in robson
- **Day 3**: Monitor for issues, verify automation
- **Day 4**: Delete infrastructure files from robson
- **Day 5**: Final verification and documentation
