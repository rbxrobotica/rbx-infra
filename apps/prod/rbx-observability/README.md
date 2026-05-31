# rbx-observability

`rbx-observability` is an in-cluster platform service for RBX telemetry integration with Langfuse.

## Prerequisites before sync

1. The image is built and pushed by the rbx-observability CI on every push to `master`
   (`ghcr.io/rbxrobotica/rbx-observability:sha-<commit>` + `:latest`, via the built-in `GITHUB_TOKEN`).
   The `newTag` here is promoted by ArgoCD Image Updater — see `docs/infra/IMAGE-PROMOTION.md`.
2. Provision the referenced `rbx-observability-token` and `rbx-observability-langfuse` secrets in `rbx-ia-br`.

The ArgoCD Application is manual-sync: the operator syncs to deploy once the secrets are provisioned.
