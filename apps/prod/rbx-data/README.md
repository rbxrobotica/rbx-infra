# rbx-data

`rbx-data` is an in-cluster platform service for RBX data lake and warehouse access.

## Prerequisites before sync

1. The image is built and pushed by the rbx-data CI on every push to `main`
   (`ghcr.io/rbxrobotica/rbx-data:sha-<commit>` + `:latest`, via the built-in `GITHUB_TOKEN`).
   Image promotion follows the fleet-standard CI self-push pattern; ArgoCD Image
   Updater annotations were retired (issue #168). See `docs/infra/IMAGE-PROMOTION.md`.
2. Provision the referenced `contabo-s3-credentials`, `rbx-data-token`, and `rbx-data-warehouse` secrets in `rbx-ia-br`.

The ArgoCD Application is manual-sync: the operator syncs to deploy once the secrets are provisioned.
