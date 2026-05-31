# rbx-data

`rbx-data` is an in-cluster platform service for RBX data lake and warehouse access.

## Prerequisites before sync

1. Build and push `ghcr.io/rbxrobotica/rbx-data:latest` to GHCR. No image-build workflow exists yet.
2. Provision the referenced `contabo-s3-credentials`, `rbx-data-token`, and `rbx-data-warehouse` secrets in `rbx-ia-br`.

The ArgoCD Application is manual-sync until the container image and secrets are ready.
