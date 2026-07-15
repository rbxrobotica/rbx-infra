# External Postgres App DB Recovery

Use this runbook when an app in k3s is `Synced` in ArgoCD but fails to start
because its external PostgreSQL database on `jaguar` is unreachable or rejects
authentication.

This is an operational runbook. Any command that changes PostgreSQL roles,
databases, `pg_hba.conf`, EndpointSlices, pods, or Secrets requires explicit
per-operation human authorization.

## Symptoms

- Pod is in `CrashLoopBackOff`.
- App log shows one of:
  - `connect: connection refused`
  - `password authentication failed`
  - `no pg_hba.conf entry`
- ArgoCD may still show `Synced`; health is the source of truth for runtime
  readiness.

## Read-only Diagnosis

Check GitOps and runtime state:

```bash
rtk kubectl get application -n argocd <app> -o wide
rtk kubectl get pod,svc,endpoints,endpointslice -n <namespace> -o wide
rtk kubectl logs -n <namespace> deploy/<deployment> --tail=120
```

For selectorless external Postgres Services, verify:

- `Service/<app>-postgres` has no `.spec.selector`.
- `Endpoints/<app>-postgres` points to `161.97.147.76:5432`.
- There is exactly one relevant EndpointSlice for that Service, managed by
  `endpointslicemirroring-controller.k8s.io`, and it points to `161.97.147.76`.
- No stale `endpointslice-controller.k8s.io` slice points to app pods or Redis.

Check the Secret shape without printing values:

```bash
rtk kubectl get secret -n <namespace> <secret> \
  -o go-template='{{.metadata.creationTimestamp}}{{"\n"}}{{range $k, $v := .data}}{{$k}}{{"\n"}}{{end}}'
```

Inspect non-secret PostgreSQL metadata on `jaguar`:

```bash
rtk ssh jaguar -- 'sudo grep -nE "<db>|<user>|<node-ip>" /etc/postgresql/16/main/pg_hba.conf'
rtk ssh jaguar -- 'sudo -u postgres psql -Atc "SELECT rolname FROM pg_roles WHERE rolname='\''<user>'\'';"'
rtk ssh jaguar -- 'sudo -u postgres psql -Atc "SELECT datname FROM pg_database WHERE datname='\''<db>'\'';"'
```

## Recovery Bootstrap

When the runtime Secret already exists and is the intended source of the app's
`DATABASE_URL`, use the bootstrap script. It reads the Secret, parses the DB/user
from the URL, aligns the PostgreSQL role password to the Secret, creates the DB
if missing, adds node-scoped `pg_hba.conf` entries, and reloads PostgreSQL.

The script never prints the password.

Dry-run first:

```bash
bootstrap/scripts/bootstrap-external-postgres-app-db.sh \
  --namespace truthmetal \
  --secret truthmetal-secrets \
  --key database-url \
  --expected-db truthmetal \
  --expected-user truthmetal \
  --hba-ip 158.220.116.31 \
  --hba-ip 173.212.246.8 \
  --hba-ip 5.189.178.212 \
  --dry-run
```

Run for `truthmetal`:

```bash
bootstrap/scripts/bootstrap-external-postgres-app-db.sh \
  --namespace truthmetal \
  --secret truthmetal-secrets \
  --key database-url \
  --expected-db truthmetal \
  --expected-user truthmetal \
  --hba-ip 158.220.116.31 \
  --hba-ip 173.212.246.8 \
  --hba-ip 5.189.178.212
```

Run for `rbx-ledger` staging:

```bash
bootstrap/scripts/bootstrap-external-postgres-app-db.sh \
  --namespace rbx-ledger \
  --secret rbx-ledger-secrets \
  --key DATABASE_URL \
  --expected-db rbx_ledger_staging \
  --expected-user rbx_ledger_staging_app \
  --hba-ip 158.220.116.31 \
  --hba-ip 173.212.246.8 \
  --hba-ip 5.189.178.212
```

## Stale EndpointSlice Recovery

If a selectorless Service previously had a selector, Kubernetes may leave a stale
EndpointSlice owned by the Service and managed by `endpointslice-controller`.
That slice can keep routing traffic to the wrong pods even after GitOps removes
the selector.

After confirming the selector is gone and the mirrored EndpointSlice exists,
delete only the stale slice:

```bash
rtk kubectl delete endpointslice -n <namespace> <stale-slice-name>
```

This is a live Kubernetes mutation and requires explicit operator approval.

## Validation

Wait for the natural CrashLoopBackOff retry or explicitly restart only with
operator approval. Then verify:

```bash
rtk kubectl get application -n argocd <app> -o wide
rtk kubectl get pod,svc,endpoints -n <namespace> -o wide
rtk kubectl logs -n <namespace> deploy/<deployment> --tail=40
```

Healthy examples:

- `truthmetal`: logs include `database connected`, `migrations applied`,
  `redis connected`, and `TruthMetal listening`.
- `rbx-ledger`: logs include `rbx-ledger listening`.

## Rollback

- The script creates a timestamped backup next to `pg_hba.conf`.
- Revert role password by re-running the script with the intended Secret.
- Remove only the app-specific `pg_hba.conf` lines that were added, then reload
  PostgreSQL.
- Drop a newly created empty database only after confirming no app data exists.
