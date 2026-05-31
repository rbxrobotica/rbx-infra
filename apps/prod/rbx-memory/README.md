# rbx-memory

`rbx-memory` is an in-cluster platform service for RBX memory storage backed by Contabo S3.

## Prerequisites before sync

1. Build and push `ghcr.io/rbxrobotica/rbx-memory:latest` to GHCR. No image-build workflow exists yet.
2. Provision the referenced `contabo-s3-credentials` and `rbx-memory-token` secrets in `rbx-ia-br`.

The ArgoCD Application is manual-sync until the container image and secrets are ready.
