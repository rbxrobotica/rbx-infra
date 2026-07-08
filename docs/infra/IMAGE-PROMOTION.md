# Image Promotion

RBX application repositories build and push images to GHCR. Promotion is owned by `rbx-infra`: the promoted image reference is recorded in GitOps state before the cluster converges.

Production ArgoCD Applications remain manual-sync unless a service-specific ADR says otherwise. Sandbox applications can be automated end to end when the risk is acceptable. In both cases, image automation only changes Git; the cluster converges from Git, not from a direct cluster mutation.

## Current transition state

As of 2026-07-08, some Applications still let ArgoCD Image Updater select the
`newest-build` and write promotion commits directly to `main`. This is a
transition state, not the target production standard.

The 2026-07-08 cluster health review found `rbx-cms` degraded because GitOps had
promoted `sha-89d6985` for `ghcr.io/rbxrobotica/rbx-cms` and
`ghcr.io/rbxrobotica/rbx-cms-web`, but the tag did not exist in GHCR at pull
time. A `Synced` ArgoCD Application can therefore still be unavailable if the
promotion path accepts a bad image reference.

## Existing Image Updater flow

The apps listed below still use the transition-state Image Updater path:

1. Application CI builds an image and pushes `ghcr.io/rbxrobotica/<service>:sha-<git-sha>`.
2. ArgoCD Image Updater runs in the `argocd` namespace.
3. Image Updater reads GHCR using `argocd/argocd-image-updater-ghcr`.
4. Image Updater commits the selected tag to `main` in `rbxrobotica/rbx-infra`.
5. ArgoCD sees the Git change, but the service Application remains manual-sync until an operator syncs it.

Before any production promotion, the chosen tag must be proven to exist in GHCR
for every image being updated. For multi-image apps, all images must exist
before any GitOps state changes.

## Applications

The Image Updater-managed Applications currently found under
`gitops/app-of-apps/` are:

- `md-prec-kulinaryos-prod` -> `ghcr.io/rbxrobotica/md-prec-kulinaryos`
- `merovelis-prod` -> `ghcr.io/rbxrobotica/merovelis-site`
- `rbx-cms` -> `ghcr.io/rbxrobotica/rbx-cms`, `ghcr.io/rbxrobotica/rbx-cms-web`
- `rbx-commerce-sandbox` -> `ghcr.io/rbxrobotica/rbx-commerce`
- `rbx-data` -> `ghcr.io/rbxrobotica/rbx-data`
- `rbx-ia-br` -> `ghcr.io/rbxrobotica/rbx-ia-br`
- `rbx-memory` -> `ghcr.io/rbxrobotica/rbx-memory`
- `rbx-observability` -> `ghcr.io/rbxrobotica/rbx-observability`
- `rbxsystems-ch` -> `ghcr.io/rbxrobotica/rbx-ia-br`
- `strategos-prod` -> `ghcr.io/rbxrobotica/strategos-site`, `ghcr.io/rbxrobotica/strategos-ui`

Single-image Applications use this annotation shape; multi-image Applications
repeat the per-image settings for each alias (`app`, `web`, `site`, `ui`):

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

## P1 tooling

Two repository-local scripts support the P1 transition:

- `scripts/promote-image-tag.sh <app> <image> <tag>` validates the app name,
  image namespace, immutable `sha-*` tag, kustomization image stanza, and
  optionally the remote GHCR manifest (`CHECK_REGISTRY=1`) before updating one
  `newTag`. It refuses partial promotion for multi-image kustomizations unless
  `ALLOW_PARTIAL_MULTI_IMAGE=1` is set explicitly.
- `scripts/report-p1-image-debt.sh` prints the current backlog: production
  `newTag: latest`, manifest `image: *:latest`, Image Updater `newest-build`,
  direct-main write-back, and ArgoCD apps tracking `targetRevision: main`.

`.github/workflows/image-update.yml` opens a promotion PR after registry
validation instead of committing directly to `main`. It does not sync ArgoCD or
mutate the cluster.

## P1 target standard

P1 moves production promotion toward a reviewed branch/PR flow:

1. CI publishes image tags.
2. A promotion job checks that every referenced tag exists in GHCR.
3. The promotion job updates `rbx-infra` on a promotion branch.
4. CI validates manifests and image conventions.
5. A human reviews and merges the promotion PR.
6. ArgoCD sync remains a separate human-gated operation for high-risk apps.

Blocking conditions for production promotion:

- `newTag: latest` in a production kustomization.
- A promoted `sha-*` tag missing from GHCR.
- Multiple images in one app promoted to different source commits without an
  explicit compatibility note.
- No rollback target.
- ArgoCD app already `Degraded` for unrelated reasons and no owner accepts the
  blast radius.
