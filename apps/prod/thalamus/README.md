# thalamus — institutional LLM control plane (pilot)

Thalamus inline mediated mode (master plan §4). Single institutional LLM
endpoint; LiteLLM is an internal backend only. No public ingress for the
pilot — cluster-internal callers only.

## Prerequisites (done in Phase 2, 2026-07-17)

- Jaguar database `thalamus`, roles `thalamus_app` / `thalamus_migrator`,
  pg_hba scoped to the cluster node IPs; migrations 0001+0002 applied.
- Credentials in `pass rbx/thalamus/`.

## Deploy (staged: observe → deploy → shadow → pilot)

1. **Secret** (out-of-band; pass is the source of truth — see
   `secrets.example.yml`):

   ```bash
   kubectl -n thalamus create secret generic thalamus-db \
     --from-literal=database-url="postgres://thalamus_app:$(pass show rbx/thalamus/db-password)@thalamus-postgres.thalamus.svc.cluster.local:5432/thalamus"
   ```

2. **Image**: pinned `sha-*` tag from the thalamus-core `build-image`
   workflow (ghcr.io/rbxrobotica/thalamus-core, SBOM + trivy CRITICAL gate).
   Bump `deploy.yml` after each release like the rest of the fleet.

3. **Sync**: the ArgoCD Application (`gitops/app-of-apps/thalamus.yml`) has
   NO automated sync — first sync is a manual, observed action.

4. **Verify**: `kubectl -n thalamus get pods` 1/1; `/readyz` must report
   `durable_audit: true` and `audit_reachable: true` (the server fails fast
   without the audit store — fail-closed by design).

## Flags

- `THALAMUS_RBX_API=off` for now; flip to `on` + set
  `THALAMUS_TOKEN_INTROSPECTION_URL` once rbx-token-service introspection is
  reachable in-cluster (Gate A surface).
- `THALAMUS_DURABLE_AUDIT=off` is the audit kill-switch (never during pilot).
- `THALAMUS_RBX_RATE_LIMIT` requests/min per caller key (default 120).

## Migrations

Owned exclusively by `thalamus_migrator`. Run `thalamus-migrate` (in the
image) with `THALAMUS_MIGRATE_DATABASE_URL` using the migrator credentials;
never grant DDL to `thalamus_app`.

## Rollback

Roll back the image tag; the legacy `/v1/*` surface stays available. Never
bypass Thalamus to restore service (no LiteLLM key redistribution).
NetworkPolicy closure of LiteLLM (deny non-Thalamus namespaces) is Gate F —
only after consumer migration.
