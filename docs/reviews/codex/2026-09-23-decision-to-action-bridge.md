# Decision-to-Action Bridge: Codex review gate

Date: 2026-09-23
Reviewer: Codex CLI 0.147.0, model gpt-5.6-sol, reasoning effort medium, read-only sandbox
Scope: `feat/decision-to-action-bridge` working tree: rbx-public-presence image pin and
`/api/v1` route, Ansible ghcr-pull-secret for rbx-public-presence, rbx-maestro kustomize
conversion plus Flight Deck admission env and ExternalSecret, rbx-flightdeck env,
ExternalSecret keys and worker CronJob, runbook and SECRETS.md.

## Result

Round 3: no findings. VERDICT: APPROVE.

## Findings resolved

### Round 1 (REQUEST_CHANGES)
1. blocking: the new `apps/prod/rbx-maestro/kustomization.yml` carried a global
   `namespace: rbx-maestro` transformer, which relocated the ESO Role and RoleBinding
   from `rbx-ia-br`; ArgoCD would have pruned the real ones and broken every Maestro
   ExternalSecret. The transformer was removed; every manifest carries its own
   `metadata.namespace`; the render now keeps both objects in `rbx-ia-br`.
2. blocking: `maestro-flightdeck-key` was missing from the Role `resourceNames`. Added.
3. should-fix: stale "api cluster-internal" comments in the app-of-apps entry and
   `api-deploy.yml`. Rewritten.
4. nit: runbook `kubectl get` with two `-n` flags. Split into two commands.

### Round 2 (REQUEST_CHANGES)
1. blocking: the Public Presence `db-migrate` hook lacked `PRESENCE_S3_PUBLIC_URL`,
   which `config.Load()` requires in production before `migrate` runs; with the real
   image the hook would fail and ArgoCD would stay blocked again. The hook now carries
   `PRESENCE_S3_BUCKET`, `CONTABO_S3_ENDPOINT` and `PRESENCE_S3_PUBLIC_URL`, mirroring
   `api-deploy.yml`.
2. nit: placeholder-tag comment in the app-of-apps entry. Updated.
3. nit: runbook said hex but used `pass generate`. Now `openssl rand -hex 32 | pass insert -e`.

Round 2 also verified: rendered Maestro inventory equals the previous plain-directory
set plus only the new ExternalSecret (nothing pruned); `git diff --check` clean; the
three affected `kubectl kustomize` builds pass.

## Invariants reviewed
- No secret value enters Git; new keys are documented in SECRETS.md and provisioned by
  the owner (runbook).
- Admission only: `AGENT_LOOP_FLIGHTDECK_DISPATCH_KEY` is not provisioned; Maestro stores
  Flight Deck missions `dispatch_blocked=true`; nothing publishes externally.
- Pattern R: no hardcoded `sha-*` in Deployments; production kustomizations pin `sha-*`.
- Traefik: `/api/v1` rule has higher priority than the console rule on the same host.
- ESO v1 API; no `ServerSideApply`.

## Evidence
- `yamllint` on all changed files: clean.
- `kubectl kustomize apps/prod/{rbx-maestro,rbx-flightdeck,rbx-public-presence}`: build ok.
- `scripts/check-image-convention.sh` on changed manifests: no violations.
- Lockstep dependency: rbx-maestro `.github/workflows/build.yml` switches promotion to
  `yq` on `apps/prod/rbx-maestro/kustomization.yml` in the same change wave.
