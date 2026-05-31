# rbx-data

`rbx-data` is an in-cluster platform service for RBX data lake and warehouse access.

## Prerequisites before sync

1. The image is built and pushed by the rbx-data CI on every push to `master`
   (`ghcr.io/rbxrobotica/rbx-data:sha-<commit>` + `:latest`, via the built-in `GITHUB_TOKEN`).
   The `newTag` here is promoted by ArgoCD Image Updater — see `docs/infra/IMAGE-PROMOTION.md`.
2. Provision the referenced `contabo-s3-credentials`, `rbx-data-token`, and `rbx-data-warehouse` secrets in `rbx-ia-br`.

The ArgoCD Application is manual-sync: the operator syncs to deploy once the secrets are provisioned.
