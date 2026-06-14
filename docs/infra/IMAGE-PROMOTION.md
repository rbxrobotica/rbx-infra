# Image Promotion

RBX application repositories build and push images to GHCR. Promotion is owned by `rbx-infra`: ArgoCD Image Updater watches the relevant GHCR repositories, detects new immutable `sha-*` tags, and writes the promoted tag into the service Kustomization in this repository.

Production ArgoCD Applications remain manual-sync. Sandbox applications can be automated end to end when the risk is acceptable. In both cases, Image Updater only changes Git; the cluster converges from Git, not from a direct cluster mutation.

## Flow

1. Application CI builds an image and pushes `ghcr.io/rbxrobotica/<service>:sha-<git-sha>`.
2. ArgoCD Image Updater runs in the `argocd` namespace.
3. Image Updater reads GHCR using `argocd/argocd-image-updater-ghcr`.
4. Image Updater commits the selected tag to `main` in `rbxrobotica/rbx-infra`.
5. ArgoCD sees the Git change, but the service Application remains manual-sync until an operator syncs it.

## Applications

The Image Updater-managed Applications are:

- `rbx-memory` -> `ghcr.io/rbxrobotica/rbx-memory`
- `rbx-observability` -> `ghcr.io/rbxrobotica/rbx-observability`
- `rbx-data` -> `ghcr.io/rbxrobotica/rbx-data`
- `rbx-commerce-sandbox` -> `ghcr.io/rbxrobotica/rbx-commerce`

Each Application uses these annotations:

```yaml
argocd-image-updater.argoproj.io/image-list: app=ghcr.io/rbxrobotica/<service>
argocd-image-updater.argoproj.io/app.update-strategy: newest-build
argocd-image-updater.argoproj.io/app.allow-tags: regexp:^sha-[0-9a-f]+$
argocd-image-updater.argoproj.io/app.kustomize.image-name: ghcr.io/rbxrobotica/<service>
argocd-image-updater.argoproj.io/write-back-method: git
argocd-image-updater.argoproj.io/git-branch: main
argocd-image-updater.argoproj.io/write-back-target: kustomization
```

`write-back-target: kustomization` is required so Image Updater changes `images[].newTag` in `kustomization.yml` instead of writing an `.argocd-source-*` override file.

## Secrets

The `k8s-secrets` Ansible role provisions the required Kubernetes secrets in the `argocd` namespace.

| Secret | pass path | Purpose |
| --- | --- | --- |
| `argocd-image-updater-ghcr` | `rbx/cluster/ghcr-token` | GHCR read token with `read:packages` for registry polling. |
| `argocd-image-updater-git-creds` | `rbx/github/rbx-infra-image-updater` | ArgoCD repository credential used by Image Updater for write-back commits to `rbxrobotica/rbx-infra`. |

No secret values are stored in Git. Operators provision the pass entries before running the bootstrap secret role.

The Application annotation uses `write-back-method: git`, so Image Updater reuses ArgoCD repository credentials for `https://github.com/rbxrobotica/rbx-infra`. The bootstrap role labels `argocd-image-updater-git-creds` as an ArgoCD repository secret for that URL.

## Review Options

The current model writes the promotion commit directly to `main`. Production deployment remains manual because the service Applications do not enable automated sync. Sandbox deployment keeps automated sync enabled so the new image lands after the Git change reconciles.

For stricter review, Image Updater can instead write to a promotion branch and open a pull request before `main` changes. A GitHub App is preferable long-term to a deploy key or PAT because its permissions, installation scope, and rotation can be managed centrally.
