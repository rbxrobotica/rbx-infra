# rbx-observability

`rbx-observability` is an in-cluster platform service for RBX telemetry integration with Langfuse.

## Prerequisites before sync

1. Build and push `ghcr.io/rbxrobotica/rbx-observability:latest` to GHCR. No image-build workflow exists yet.
2. Provision the referenced `rbx-observability-token` and `rbx-observability-langfuse` secrets in `rbx-ia-br`.

The ArgoCD Application is manual-sync until the container image and secrets are ready.
