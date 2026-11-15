# Image Promotion

RBX application repositories build and push images to GHCR. Promotion is owned
by `rbx-infra`: the promoted image reference is recorded in GitOps state
(`apps/*/kustomization.yml` `images[].newTag`) and the cluster converges from
Git, never from a direct cluster mutation.

## The standard: CI self-push (fleet-wide since 2026-08-02)

The repository that builds an image is the one that promotes it. After the
image push on the default branch, the same CI job:

1. Checks out `rbxrobotica/rbx-infra` with the `RBX_INFRA_PAT` secret.
2. Bumps `images[].newTag` in the owning overlay(s) with `yq` (or `sed`),
   using the exact tag it just pushed.
3. Commits as `github-actions[bot]` and pushes to `main`.

Template (single image; repeat the `yq` line per image for multi-image apps):

```yaml
      # Promote image tag to rbx-infra (fleet standard, rbx-infra issue #168)
      - uses: actions/checkout@v4
        if: github.event_name == 'push' && github.ref == 'refs/heads/main'
        with:
          repository: rbxrobotica/rbx-infra
          token: ${{ secrets.RBX_INFRA_PAT }}
          path: rbx-infra
      - name: Promote image tag to rbx-infra
        if: github.event_name == 'push' && github.ref == 'refs/heads/main'
        run: |
          cd rbx-infra
          K=apps/prod/<app>/kustomization.yml
          yq -i '(.images[] | select(.name == "ghcr.io/rbxrobotica/<image>") | .newTag) = "sha-${{ github.sha }}"' "$K"
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add "$K"
          git diff --cached --quiet || git commit -m "chore(<app>): bump image to sha-${{ github.sha }}"
          git push
```

The `if` guard is mandatory when the workflow also runs on pull requests.
Repos that gate the whole job on push-to-main may omit per-step guards.
Workflows that clone rbx-infra over SSH (rbx-systems-frontend) use their
read-write deploy key instead of the PAT; the contract is identical.

Why this pattern is the standard:

- Every deploy is an auditable commit in rbx-infra (author, time, trigger).
- A broken promotion turns the app repo's CI red on the merge commit. The
  failure is loud and attributable, never a silent freeze.
- Because the promoting run is the run that pushed the image, the promoted
  tag always exists in GHCR (this structurally prevents the 2026-07-08
  rbx-cms incident, where a nonexistent tag was promoted).
- Floating tags are forbidden: overlays pin `sha-*` tags only
  (Pattern R rule 3; `newTag: latest` is a violation).

## Adopting repos

All image-producing repos promote via CI as of 2026-08-02: robson,
rbx-console, rbx-commerce (prod + sandbox), rbx-systems-frontend (rbx-ia-br +
rbxsystems-ch), rbx-cms (app + web), rbx-data, rbx-memory, rbx-observability,
strategos-site, strategos-ui, merovelis-site, md-prec-kulinaryos,
rbx-flightdeck (since the Flight Deck deploy PR; requires adding
rbx-flightdeck to the RBX_INFRA_PAT org-secret repository visibility).

## Credentials

`RBX_INFRA_PAT` (fine-grained PAT with write on rbx-infra; value in
`pass rbx/github/rbx-infra-write-pat`) is provided as an org secret with
selected-repository visibility, plus repo-level copies where org visibility
could not be extended. When rotating the PAT, update the org secret AND the
repo-level copies (strategos-site, strategos-ui, merovelis-site, rbx-cms,
md-prec-kulinaryos, rbx-commerce). No secret values are stored in Git.

A GitHub App remains preferable long-term to a deploy key or PAT because its
permissions, installation scope, and rotation can be managed centrally.

## P1 tooling

Two repository-local scripts support stricter promotion review:

- `scripts/promote-image-tag.sh <app> <image> <tag>` validates the app name,
  image namespace, immutable `sha-*` tag, kustomization image stanza, and
  optionally the remote GHCR manifest (`CHECK_REGISTRY=1`) before updating one
  `newTag`. It refuses partial promotion for multi-image kustomizations unless
  `ALLOW_PARTIAL_MULTI_IMAGE=1` is set explicitly, and it refuses an entry that
  pins a `digest` (next section).
- `scripts/report-p1-image-debt.sh` prints the current backlog: production
  `newTag: latest`, manifest `image: *:latest`, direct-main write-back, and
  ArgoCD apps tracking `targetRevision: main`.

## Digest-pinned entries

Some overlays set `digest:` beside `newTag:` so that a moved tag cannot change
what runs. Kustomize renders such an entry as `image:tag@digest` and the
cluster pulls by digest; the tag is only a label. A promotion therefore changes
`newTag` and `digest` in the same commit. Changing `newTag` alone keeps the old
image running under the new tag's name.

`scripts/promote-image-tag.sh` refuses an entry that carries a digest, so
`.github/workflows/image-update.yml` fails instead of opening a promotion PR
for it. Promote that image by hand. Take the digest from the build that pushed
the tag, whose log prints it; a registry lookup of the tag only shows what the
tag points at when you ask. The self-push template above also writes only
`newTag`: do not use it on an entry that carries a digest.

The script edits by line and then asks a YAML parser whether the edit may be
installed (`scripts/promote-image-tag-verify.py`). A digest reaches an image in
more ways than a `digest:` line under its name: a key in another letter case, a
second entry or a second `images` key for the same image, the deprecated
`imageTags`, escapes, tags, merge keys and aliases, an entry for the name that
`newName` gave the image, and an entry whose name matches the image as a
pattern (kustomize reads `rbx.commerce` as also meaning `rbx-commerce`). The
verifier refuses a kustomization that uses any of them, and accepts an edit only
when the parsed file differs from the original in that one `newTag`. It reads
the `images` list of that kustomization and nothing else: an image set by a
patch, a replacement or a transformer file is outside what it checks.

The verifier runs in its own environment: a virtual environment of the
machine's python3 with PyYAML pinned by hash in
`scripts/promote-image-tag-requirements.txt`. Provision it once and hand it to
the script, as `image-update.yml` does:

```bash
bash scripts/promote-image-tag-env.sh /tmp/promote-image-tag-env
PROMOTE_IMAGE_TAG_ENV=/tmp/promote-image-tag-env \
  bash scripts/promote-image-tag.sh <app> <image> <tag>
```

The script refuses to run without it, and refuses an environment provisioned
from other pins.

The script refuses instead of looking the new digest up. A digest read from the
registry at promotion time pins whatever the tag points at in that moment,
which is the movement the pin exists to rule out, and nothing in this
repository tests that lookup against a real registry. The price: a
digest-pinned image gets no automated promotion PR. Automating it needs the
digest to come from the build that pushed the image, a check of that digest
against the registry, and a test that runs against a registry.

## P1 target standard (future direction)

The current standard writes the promotion commit directly to `main`. P1 moves
production promotion toward a reviewed branch/PR flow for high-risk apps:

1. CI publishes image tags.
2. A promotion job checks that every referenced tag exists in GHCR.
3. The promotion job updates `rbx-infra` on a promotion branch.
4. CI validates manifests and image conventions.
5. A human reviews and merges the promotion PR.
6. ArgoCD sync remains a separate human-gated operation for high-risk apps.

Blocking conditions for production promotion:

- `newTag: latest` in a production kustomization.
- A promoted `sha-*` tag missing from GHCR.
- A `newTag` changed beside an unchanged `digest` in the same `images` entry.
- Multiple images in one app promoted to different source commits without an
  explicit compatibility note.
- No rollback target.
- ArgoCD app already `Degraded` for unrelated reasons and no owner accepts the
  blast radius.

## History: ArgoCD Image Updater (retired 2026-08-02)

Between 2026-06 and 2026-07 ten Applications delegated promotion to the
argocd-image-updater controller via Application annotations (`newest-build`
strategy, git write-back). On 2026-07-28 a chart upgrade replaced the
controller with the CRD-based version, which ignores annotations and logged
`No ImageUpdater CRs to process`. Promotion for all ten apps silently stopped
while every dashboard stayed `Synced/Healthy`; the freeze was only noticed on
2026-08-01 when a deploy was independently verified. Post-mortem and
migration: rbx-infra issue #168.

The controller (`platform/image-updater/`), its Application annotations, and
the overlay comments referencing it were removed. Credentials that served it
are decommission candidates once the removal syncs: the `argocd` namespace
secrets `argocd-image-updater-ghcr` and `argocd-image-updater-git-creds`, the
rbx-infra deploy key `image-updater-ed25519-2026-06-21`, the pass entry
`rbx/github/rbx-infra-image-updater`, and the `k8s-secrets` Ansible role
tasks that provision them.
