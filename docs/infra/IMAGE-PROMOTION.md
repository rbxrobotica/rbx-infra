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

### Changes that do not go through the script

A hand edit and the self-push never call `scripts/promote-image-tag.sh`. For
them, `validate.yml` runs `scripts/check-digest-pin-promotion.sh` (job
`image-digest-pins`). It compares the kustomizations under `apps/` of two
commits and fails when an `images` entry has a new `newTag` beside the `digest`
it already had. A `newTag` added to an entry that had only the digest counts:
that is what the `yq` line of the self-push template does to such an entry. On
a pull request the job runs on the merge commit GitHub makes for it, so the two
commits are the tip of the base branch in that merge and the result of the
merge: what merging would change on `main`. On a push to `main` they are the
commit before the push and its head. Before opening a pull request, the same
script compares the branch with the commit where it left `main`:

```bash
bash scripts/check-digest-pin-promotion.sh origin/main...HEAD
```

It reads both versions by the verifier's rules, in the same pinned environment,
and it also fails when it cannot tell what the new version runs:

- a kustomization written in one of the ways the verifier refuses;
- a value of an entry that YAML does not read as a string (`newTag: null` is no
  tag to kustomize, `newTag: "null"` is one), a `newTag` that is not a tag, a
  `digest` that is not a sha256 digest (kustomize pastes them into the
  reference, so `newTag: new@sha256:...` pins as well as the two keys do);
- a symbolic link or a submodule anywhere under `apps/`, whether it changed or
  not. Kustomize reads through a link and this check does not, so what the
  link leads to can change while no kustomization file does. The tree has
  neither today, and the job stays red for every change while it has one;
- two kustomization files in one directory;
- entries that kustomize could apply to one image, one after another, when one
  of them has a digest. Kustomize applies the entries in their order, each to
  what the one before left, and the check does not work out the result.

It fails as well when it cannot tell what the previous version ran and the new
one has an entry with a tag and a digest. There are two cases. If the previous
version can be read and has entries like those of the last item, an entry of
the new version passes when its digest is one the file did not have. If the
previous version cannot be read, or was behind a symbolic link or a submodule,
every entry with a tag and a digest is refused, whatever the digest. Either
kind of previous version only reaches `main` through a change this check
refused, and the change that repairs it may be refused once more: it needs a
person to read both.

It passes an entry whose tag and digest changed together, one that gained or
lost its digest, and one without a digest. `newName` is allowed here.

What the check does not do:

- Stop a push to `main`. ArgoCD syncs `main` by itself, so when the job turns
  red on a self-push the entry may already be deployed, the old image under the
  new tag. The red run is the notice: set the digest of that tag, or revert.
- Run on a push, or on a pull request, whose head commit says `[skip ci]`.
  GitHub starts no workflow for it. On a pull request the next commit without
  the marker starts the job again, and its comparison covers the skipped ones.
  On `main` it does not: the next push is compared with the commit before it,
  which is the skipped one. As of 2026-11-14 the self-pushes of robson-v2,
  robson-frontend-v2 and the rbx-ia-br site write that marker; none of their
  entries carries a digest. Take the marker out of a pipeline before pinning
  its image by digest.
- Tell whether the digest is the digest of the tag. It compares two commits and
  asks no registry. A tag and a digest that changed together pass, whatever
  they are, and so does a digest that changed alone.
- Accept a new tag whose build published the same contents. That build has the
  old digest, and the change is refused although it is right. Nothing would be
  deployed by it: keep the old tag.
- See a digest that comes from outside the `images` list of that kustomization
  (a patch, a replacement, a transformer file, a component, a base), a tag that
  a manifest carries, or a kustomization outside `apps/`. It does not follow an
  application directory that was moved in the same change either, or an entry
  whose name changed to one that cannot match what the old one matched.
- Remember. An entry that drops its digest in one change and gets it back
  beside a new tag in the next passes both.

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
