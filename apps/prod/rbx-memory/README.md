# rbx-memory

`rbx-memory` is an in-cluster platform service for RBX memory storage backed by Contabo S3.

## Prerequisites before sync

1. The image is built and pushed by the rbx-memory CI on every push to `master`
   (`ghcr.io/rbxrobotica/rbx-memory:sha-<commit>` + `:latest`, via the built-in `GITHUB_TOKEN`).
   The `newTag` here is promoted by ArgoCD Image Updater — see `docs/infra/IMAGE-PROMOTION.md`.
2. Provision the referenced `contabo-s3-credentials` and `rbx-memory-token` secrets in `rbx-ia-br`.

The ArgoCD Application is manual-sync: the operator syncs to deploy once the secrets are provisioned.
