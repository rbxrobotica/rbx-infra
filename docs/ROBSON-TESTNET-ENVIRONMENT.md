# Robson v3 — Testnet Environment

**Status:** Live — operational as of 2026-04-15
**Date:** 2026-04-12
**Revised:** 2026-04-15 — R7: deployed, ROBSON_ENV corrected to `production`, ROBSON_API_TOKEN wired, migration index conflicts resolved
**Decision:** See `docs/adr/ADR-0003-robson-testnet-isolation.md`

---

## Purpose

This document is the authoritative specification for the `robson-testnet` environment.
It defines exactly what must exist, where, and with which values.

This is not a generic multi-environment framework for all RBX products.
It is a concrete instantiation for **Robson v3 on the Binance testnet**, designed
to allow safe exchange validation before committing real capital.

The document serves as the implementation brief for whoever executes the work.

---

## What is being isolated

Robson v3 executes trades on the Binance exchange. Two exchange endpoints exist:

| Endpoint | URL | Capital at risk |
|----------|-----|----------------|
| Production | `https://api.binance.com` | Real funds |
| Testnet | `https://testnet.binance.vision` | Synthetic funds only |

The testnet environment ensures the daemon **can only ever connect to the testnet endpoint**.
This guarantee must be structural — embedded in the deployment artifact — not behavioral.

---

## Environment definition

### Namespace

```
robson-testnet
```

Labels: `app.kubernetes.io/part-of: robson`, `environment: testnet`

The namespace is the primary isolation boundary. All resources for this environment
live inside it.

**Namespace location:** `apps/testnet/robson/namespace.yml` (sync-wave: -1 annotation).
This follows the same pattern as production (`apps/prod/robson/namespace.yml`).
The namespace is NOT placed in `core/namespaces/` because that directory has no ArgoCD
Application managing it — files there are never synced to the cluster.
The ArgoCD Application `robson-testnet` also specifies `CreateNamespace=true` as a safety net.

**Exception — shared physical database host:** The ParadeDB instance on `jaguar`
(161.97.147.76) is shared at the host level. The testnet uses a separate logical database
(`robson_testnet`) and a separate Service + Endpoints resource (`paradedb-svc.yml`) inside
the `robson-testnet` namespace. No service or resource from the `robson` (production)
namespace is referenced directly. The testnet daemon connects to
`robson-paradedb.robson-testnet.svc.cluster.local:5432`, not to the production service.

### Directory in rbx-infra

```
apps/testnet/robson/
```

This path is the ArgoCD source for the testnet environment. It is entirely separate from
`apps/prod/robson/` — no shared base, no kustomize overlay on top of prod.
If a file needs to exist in both environments, it must be duplicated explicitly.
Sharing files between prod and testnet paths is forbidden.

### ArgoCD Application

```
name: robson-testnet
source.path: apps/testnet/robson
destination.namespace: robson-testnet
```

The `robson-testnet` ArgoCD Application is independent of `robson-prod`.
Its destination namespace must never be changed to `robson`.

### ConfigMap (`robsond-config`)

The ConfigMap in `apps/testnet/robson/robsond-config.yml` must contain:

| Key | Value | Reason |
|-----|-------|--------|
| `ROBSON_ENV` | `production` | Daemon only accepts `test`, `development`, `production`; exchange routing is controlled by `ROBSON_BINANCE_USE_TESTNET` |
| `ROBSON_BINANCE_USE_TESTNET` | `"true"` | Activates `BinanceRestClient::testnet()` constructor |
| `ROBSON_POSITION_MONITOR_ENABLED` | `"true"` | Enabled in testnet for full behavioral validation |
| `PROJECTION_STREAM_KEY` | `robson:testnet` | Isolated event stream, does not overlap with production |
| `PROJECTION_POLL_INTERVAL_MS` | `"500"` | Standard polling rate |
| `RUST_LOG` | `robsond=debug,robson_engine=debug,...` | Debug verbosity appropriate for testnet |
| `ROBSON_API_HOST` | `0.0.0.0` | Same as production |
| `ROBSON_API_PORT` | `8080` | Same as production |

**`ROBSON_BINANCE_USE_TESTNET: "true"` is the critical key.** It must always be present
in the testnet ConfigMap. Its absence means the daemon would call `BinanceRestClient::new()`
(production endpoint) with testnet credentials — a misconfigured but potentially dangerous state.

### Secret (`robsond-testnet-secret`)

The secret in namespace `robson-testnet` must contain exactly these keys:

| Secret key | Content | pass source |
|-----------|---------|-------------|
| `binance-api-key` | Binance testnet API key | `rbx/robson-testnet/binance-api-key` |
| `binance-api-secret` | Binance testnet API secret | `rbx/robson-testnet/binance-api-secret` |
| `database-url` | `postgresql://robson_testnet:{password}@robson-paradedb.robson-testnet.svc.cluster.local:5432/robson_testnet` | `rbx/robson-testnet/db-password` |
| `projection-tenant-id` | UUID (distinct from production tenant) | `rbx/robson-testnet/projection-tenant-id` |
| `api-token` | Bearer token for robsond HTTP API (`/arm`, `/signal`, etc.) | `rbx/robson-testnet/api-token` |

**Note on `database-url` password:** The `db-password` must use only hex characters (`openssl rand -hex 32`). Base64-encoded passwords contain `/`, `+`, `=` which break URL parsing in the PostgreSQL client.

The `database-url` uses `robson-paradedb.robson-testnet.svc.cluster.local` — the Service defined
in `apps/testnet/robson/paradedb-svc.yml` within the testnet namespace. This avoids referencing
the production `robson-paradedb` service in the `robson` namespace.

This secret is bootstrapped by the Ansible `k8s-secrets` role, testnet task block.
It must never be created or modified with `kubectl` directly.

### Database

A separate logical database on the existing ParadeDB instance on `jaguar`:

```
host (k8s service):  robson-paradedb.robson-testnet.svc.cluster.local:5432
physical host:       jaguar (161.97.147.76)
database:            robson_testnet
user:                robson_testnet
password:            from rbx/robson-testnet/db-password
```

The Service + Endpoints for this connection are defined in `apps/testnet/robson/paradedb-svc.yml`.
This service lives in the `robson-testnet` namespace and points directly to jaguar's IP.
It is completely separate from the `robson-paradedb` service in the `robson` namespace.

The database must be created before first deployment. The Ansible bootstrap is the right place
for this step (a one-time `CREATE DATABASE` and `GRANT` in the ParadeDB provisioning role or
a standalone task). It is not created automatically by the daemon.

The production database (`robson_v2` for the Rust daemon) must never be referenced
in the testnet environment.

### Projection stream key

```
PROJECTION_STREAM_KEY: "robson:testnet"
```

This key is used by the projection subsystem to scope event reads. Using a different key
from production (`robson:daemon`) ensures testnet events are isolated in the event log.
If the two environments ever shared the same stream key on the same database, events
from one would be visible to the other — which would corrupt projections.

### Image

The testnet environment uses the same container image as production:

```
ghcr.io/rbxrobotica/robson-v2:{sha-tag}
```

The SHA tag must be pinned explicitly. Never use `:latest` or any mutable tag.
The environment difference is in configuration (ConfigMap + Secret), not in the image.

---

## Code change required in Robson

Before the testnet environment can function correctly, one change is required in the
`robson` Rust codebase:

**The daemon must read `ROBSON_BINANCE_USE_TESTNET` and conditionally call the correct
`BinanceRestClient` constructor.**

The `BinanceRestClient::testnet()` constructor already exists in
`v2/robson-connectors/src/binance_rest.rs`. It switches the base URL to
`https://testnet.binance.vision`. The wiring layer (daemon bootstrap / main.rs) must be
updated to call it when `ROBSON_BINANCE_USE_TESTNET=true`.

Without this change, the daemon ignores the ConfigMap key and always uses the production
endpoint regardless of environment. The testnet environment cannot function correctly
until this code change is deployed.

This is a single conditional in the wiring layer — no new abstractions, no refactor.

---

## Ansible bootstrap additions

**Status: implemented** — the testnet task block is in `bootstrap/ansible/roles/k8s-secrets/tasks/main.yml`.

The block:

1. Reads `rbx/robson-testnet/binance-api-key` and `rbx/robson-testnet/binance-api-secret`
2. Reads `rbx/robson-testnet/db-password`
3. Generates and stores `rbx/robson-testnet/projection-tenant-id` on first run (idempotent)
4. Constructs the `database-url` using `robson-paradedb.robson-testnet.svc.cluster.local:5432`
5. Creates `robsond-testnet-secret` in namespace `robson-testnet`
6. Creates `ghcr-pull-secret` in namespace `robson-testnet` (same GHCR token as production)

Defaults for these tasks are in `bootstrap/ansible/roles/k8s-secrets/defaults/main.yml`
under the `robsond_testnet_namespace` and `pass_robson_testnet_*` keys.

The testnet task block is completely independent of the production task block.
Pass key references in the testnet block only reference `rbx/robson-testnet/` paths.

---

## Files to create in rbx-infra

All files listed below exist in the repository as of the R1–R6 revision.

| File | Description |
|------|-------------|
| `apps/testnet/robson/namespace.yml` | Namespace `robson-testnet` with `environment: testnet` label, sync-wave: -1 |
| `apps/testnet/robson/kustomization.yml` | Kustomization listing all resources in this directory |
| `apps/testnet/robson/robsond-rbac.yml` | ServiceAccount, Role, RoleBinding scoped to `robson-testnet` |
| `apps/testnet/robson/robsond-config.yml` | ConfigMap with all testnet env vars (includes `ROBSON_BINANCE_USE_TESTNET: "true"`) |
| `apps/testnet/robson/paradedb-svc.yml` | Service + Endpoints for ParadeDB in `robson-testnet` namespace (jaguar 161.97.147.76:5432) |
| `apps/testnet/robson/robsond-deploy.yml` | Deployment referencing `robsond-testnet-secret` and `robsond-config` |
| `apps/testnet/robson/robsond-svc.yml` | Service (ClusterIP, port 8080) |
| `gitops/app-of-apps/robson-testnet.yml` | ArgoCD Application |

**Why namespace is in `apps/testnet/robson/` and not `core/namespaces/`:**
`core/namespaces/` has no ArgoCD Application managing it. Files placed there are never
synced to the cluster. The namespace must live inside the ArgoCD source path or be
created by `CreateNamespace=true`. Both are applied here: the `namespace.yml` with
sync-wave -1 ensures the namespace exists before other resources, and the ArgoCD
Application also has `CreateNamespace=true` as a safety net.

**Ansible task block:** inline in `bootstrap/ansible/roles/k8s-secrets/tasks/main.yml`,
with defaults in `bootstrap/ansible/roles/k8s-secrets/defaults/main.yml`.

No HTTPRoute or Ingress is required in Phase 1. The daemon API does not need to be externally
accessible for testnet validation. Add it in Phase 2 if operational access is needed.

---

## Deployment checklist (for GLM)

Before deploying, verify these preconditions:

- [ ] Binance testnet credentials exist in pass (`rbx/robson-testnet/binance-api-key`, `rbx/robson-testnet/binance-api-secret`)
- [ ] Testnet DB password exists in pass (`rbx/robson-testnet/db-password`)
- [ ] `robson_testnet` database and user created on ParadeDB (`jaguar`)
- [ ] Robson Rust code change for `ROBSON_BINANCE_USE_TESTNET` is merged and image is built
- [ ] New image SHA is available in GHCR (`ghcr.io/rbxrobotica/robson-v2:{sha}`)

Deployment sequence:

1. Ensure `robson_testnet` database and user exist on ParadeDB (jaguar) — run `CREATE DATABASE` / `GRANT` manually or via Ansible before this step.
2. Commit all files in `apps/testnet/robson/` and `gitops/app-of-apps/robson-testnet.yml` — this is a single atomic commit. ArgoCD will not sync until the Application manifest is committed.
3. Run Ansible k8s-secrets role to create `robsond-testnet-secret` and `ghcr-pull-secret` in `robson-testnet`:
   ```bash
   ansible-playbook bootstrap/ansible/site.yml -i bootstrap/ansible/inventory/hosts.yml
   ```
   The namespace is created by ArgoCD (step 2) or by the `Ensure robson-testnet namespace exists` task in the Ansible role — whichever runs first is fine; both are idempotent.
4. Update `apps/testnet/robson/robsond-deploy.yml` — replace `sha-REPLACE_ME` with the actual image SHA from GHCR, then commit.
5. Verify ArgoCD sync: `argocd app sync robson-testnet` or wait for automated sync.
6. Verify: `kubectl -n robson-testnet get pods`
7. Verify: daemon logs show connection to `testnet.binance.vision`, not `api.binance.com`
   ```bash
   kubectl -n robson-testnet logs deployment/robsond | grep binance
   ```

---

## Operational guardrails

### What is permitted in `robson-testnet`

- Deploying any SHA-tagged image from GHCR
- Updating the testnet ConfigMap with non-credential values
- Restarting the daemon (`kubectl rollout restart`)
- Connecting to the testnet API for debugging (`kubectl port-forward`)
- Running migrations against `robson_testnet` database
- Rotating testnet Binance credentials (via pass + Ansible k8s-secrets role)

### What is forbidden in `apps/prod/robson/`

| Forbidden | Why |
|-----------|-----|
| `ROBSON_BINANCE_USE_TESTNET` key (any value) | Incident — see below |
| Any reference to `rbx/robson-testnet/` pass paths | Credential cross-contamination |
| Any reference to `robsond-testnet-secret` | Wrong secret namespace/scope |
| Any reference to `robson_testnet` database | Wrong database |
| `PROJECTION_STREAM_KEY: "robson:testnet"` | Would pollute the production event stream |

### What constitutes an incident

The following conditions require immediate investigation and rollback:

1. `ROBSON_BINANCE_USE_TESTNET` appears in any file under `apps/prod/robson/` — regardless of value
2. `robsond-secret` (production secret) is referenced from a deployment in `robson-testnet`
3. The production daemon logs show connection to `testnet.binance.vision`
4. The testnet daemon logs show connection to `api.binance.com`
5. Both ArgoCD Applications (`robson-prod` and `robson-testnet`) point to the same source path

### Drift detection

| Symptom | Likely cause | Remediation |
|---------|-------------|-------------|
| Testnet daemon connects to `api.binance.com` | `ROBSON_BINANCE_USE_TESTNET` missing from ConfigMap, or code change not deployed | Re-check ConfigMap; verify image has the conditional wiring |
| Production daemon connects to `testnet.binance.vision` | `ROBSON_BINANCE_USE_TESTNET` was added to prod ConfigMap | Remove key, reconcile via ArgoCD, investigate how it got there |
| Testnet projection reads production events | `PROJECTION_STREAM_KEY` set to `robson:daemon` in testnet | Correct ConfigMap; wipe and replay testnet projection |
| Two deployments with same `PROJECTION_TENANT_ID` | UUID was copied instead of generated independently | Generate new UUID for testnet via Ansible task; update secret |

---

## What this is not

This document and the `robson-testnet` environment are specific to Robson v3.
They are not:

- A generic multi-environment framework for all RBX products
- A definition of how `staging` works
- A blueprint that other products must follow immediately

The pattern (dedicated namespace, isolated secrets, isolated database, isolated stream key,
environment-marker ConfigMap key) is reusable. But adoption by other products is a separate
decision that should happen when those products have a concrete need, not speculatively.

---

## Related documents

| Document | Purpose |
|----------|---------|
| `docs/adr/ADR-0003-robson-testnet-isolation.md` | Decision record: why namespace isolation, why not second cluster |
| `docs/infra/ARCHITECTURE.md` | Environment model in the overall architecture |
| `docs/infra/SECRETS.md` | pass namespace structure and k8s-secrets bootstrap for testnet |
