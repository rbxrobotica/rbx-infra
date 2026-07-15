# Database Architecture

**Date:** 2026-04-07
**Status:** Active

## Single database host: jaguar (161.97.147.76)

All application databases run on jaguar as ParadeDB (PostgreSQL-compatible).
No application should run its own database StatefulSet inside k3s.

## Databases

| Database | User | Application |
|---|---|---|
|----------|------|-------------|
| robson | robson | robsond (Rust production daemon) |
| robson_testnet | robson_testnet | robsond testnet daemon |
| truthmetal | truthmetal | Truthmetal |
| litellm | litellm | LLM Gateway (LiteLLM Proxy — experimental) |
| pdns | pdns | PowerDNS (used by pantera/ns1) |
| rbx_btcpay | rbx_btcpay | BTCPay Server (ADR-0009 Layer 2) |
| rbx_ledger_staging | rbx_ledger_staging_app | RBX Ledger staging backend |

## Connection pattern for k8s apps

Apps should not connect directly to jaguar IP unless a product-specific bootstrap/runbook says so. Prefer a selectorless Service plus a manually declared Endpoints object in the application namespace:

```yaml
# Example: apps/prod/truthmetal/postgres-svc.yml
apiVersion: v1
kind: Service
metadata:
  name: truthmetal-postgres
  namespace: truthmetal
spec:
  ports:
    - port: 5432
---
apiVersion: v1
kind: Endpoints
metadata:
  name: truthmetal-postgres
  namespace: truthmetal
subsets:
  - addresses:
      - ip: 161.97.147.76
    ports:
      - port: 5432
```

The application `DATABASE_URL` points to the Service name, for example `truthmetal-postgres:5432`, not to jaguar IP directly. This decouples apps from the infrastructure IP.

Selectorless Services must stay selectorless. If a Kustomize label transformer injects a selector into an external DB Service, Kubernetes creates EndpointSlices from matching pods and may route PostgreSQL traffic to application or Redis pods. For external DB Services, the expected EndpointSlice is mirrored from the Endpoints object and managed by `endpointslicemirroring-controller.k8s.io`.

Some staging-first or internal apps may temporarily reference jaguar directly in `DATABASE_URL`. In that case the DB/role/`pg_hba.conf` contract is still the same: dedicated DB, dedicated role, node-scoped source IPs, and no shared app credentials.

## Provisioning

The `paradedb` Ansible role creates databases and users on jaguar.
The `pdns-database` Ansible role creates the pdns database for PowerDNS.

Both are in `bootstrap/ansible/roles/` and run from `site.yml`.

Passwords live in `bootstrap/ansible/group_vars/vault.yml` (gitignored, ansible-vault encrypted).

## Operational bootstrap and drift recovery

Runtime Kubernetes Secrets are often the effective source of the application `DATABASE_URL`. When the Postgres role password, database, or `pg_hba.conf` drifts from that Secret, use the operational bootstrap:

```bash
bootstrap/scripts/bootstrap-external-postgres-app-db.sh \
  --namespace <namespace> \
  --secret <secret-name> \
  --key <database-url-key> \
  --expected-db <database> \
  --expected-user <role> \
  --hba-ip 158.220.116.31 \
  --hba-ip 173.212.246.8 \
  --hba-ip 5.189.178.212
```

The script reads the Secret, parses the DB and role, aligns the role password to the Secret value without printing it, creates the database when missing, adds node-scoped `pg_hba.conf` entries, backs up `pg_hba.conf`, and reloads Postgres.

Run `--dry-run` first and get explicit operator authorization before executing the mutating run. Full procedure: `docs/runbooks/EXTERNAL-POSTGRES-APP-DB-RECOVERY.md`.

## Access control

jaguar's `pg_hba.conf` is scoped per database: each app's user can only access
its own database. Cross-database access is not permitted.
Pantera (ns1) connects to jaguar port 5432 for the `pdns` database only.
Eagle (ns2) does NOT connect to jaguar — it uses the bind backend (AXFR only).

## Lesson from 2026-04-07 migration

During the pantera/eagle DNS split, a `truthmetal-postgres` StatefulSet was found
running on pantera with a local-path PVC. This StatefulSet was NOT in the kustomization
and was never managed by ArgoCD — it was an orphan from an earlier manual deployment.

The application was already connecting to jaguar via the External Service. The
StatefulSet was deleted and the PVC reclaimed as part of the pantera drain.

**Rule:** Never create database StatefulSets inside k3s. All persistent relational
storage goes to jaguar. The External Service pattern is the correct abstraction.


## Lesson from 2026-07-12 truthmetal/rbx-ledger recovery

`truthmetal` was `Synced` but unhealthy because a stale EndpointSlice still pointed `truthmetal-postgres` at application/Redis pods after the Service selector was removed. The stale controller-managed EndpointSlice had to be deleted after the mirrored EndpointSlice to `161.97.147.76:5432` existed.

After that, `truthmetal` reached Postgres but failed SASL auth because the role password had drifted from the Kubernetes Secret. Aligning the Postgres role to the Secret restored the service.

`rbx-ledger` staging had the same external-db contract gap in a different layer: the Secret referenced `rbx_ledger_staging_app` / `rbx_ledger_staging`, but the role/database and node-scoped `pg_hba.conf` entries were missing on jaguar. Provisioning those from the Secret restored backend readiness.

**Rule:** `Synced` is not enough. For external DB incidents, verify Service selectors, EndpointSlices, Secret key shape, Postgres role/database existence, node-scoped `pg_hba.conf`, and app logs.
