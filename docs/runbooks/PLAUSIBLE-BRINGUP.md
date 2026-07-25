# Runbook: Plausible CE bring-up

Self-hosted web analytics for `rbx.ia.br` and `rbxsystems.ch`, served from
`plausible.rbxsystems.ch`.

- Manifests: `apps/prod/plausible/`
- ArgoCD Application: `gitops/app-of-apps/plausible.yml` (manual sync)
- Upstream: `plausible/community-edition` branch `v3.2.1`

The frontend side is already done: both site deployments export
`VITE_PLAUSIBLE_DOMAIN`, `VITE_PLAUSIBLE_SCRIPT_SRC` and
`VITE_PLAUSIBLE_API_HOST` pointing at `plausible.rbxsystems.ch`, and
`+layout.server.ts` reads them at runtime. Today those requests fail because the
host does not exist. No frontend change or redeploy is part of this bring-up.

## Architecture

| Piece | Where | Why |
| --- | --- | --- |
| Plausible app | Deployment, 1 replica, node `jaguar` | Single writer, node-local PVC |
| Postgres (sites, users, settings) | External `161.97.147.76:5432` | Postgres is never run inside k3s in production |
| ClickHouse (events) | StatefulSet in-cluster, node `jaguar` | Matches the Langfuse precedent; the external-Postgres rule does not cover ClickHouse |
| TLS | cert-manager `letsencrypt-prod` | Same as every other public host |
| Ingress | Traefik IngressRoute | Forwards `X-Forwarded-For` and WebSocket upgrade by default |

Both pods are pinned to `jaguar` with a toleration for
`robson.io/dedicated=analytics:NoSchedule`. jaguar has 24Gi RAM, 8 CPU and 394Gi
of disk and sits near-idle, while altaica, sumatrae and tiger run at 60-86%
memory. Pinning also keeps the Postgres traffic on the same node as Postgres.

## Prerequisites (operator actions)

These three steps involve credentials or DNS, so they are not automated here.

### 1. Postgres role and database

On the external instance, as a superuser. Choose a strong password and keep it in
`pass`; do not echo it.

```sql
CREATE ROLE plausible LOGIN CREATEDB PASSWORD '<pw>';
CREATE DATABASE plausible OWNER plausible;
```

`CREATEDB` is required because the container's start command runs
`/entrypoint.sh db createdb` on every boot. It is idempotent.

### 2. Secrets

Generate the two application secrets and store them in `pass` without printing
them:

```bash
openssl rand -base64 48 | pass insert -m rbx/plausible/secret_key_base
openssl rand -base64 32 | pass insert -m rbx/plausible/totp_vault_key
# the Postgres password chosen in step 1
pass insert rbx/plausible/postgres_password
```

`SECRET_KEY_BASE` must be at least 64 bytes (48 raw bytes base64-encoded is 64
characters, which satisfies it).

Then create the source Secret in `rbx-ia-br`, which is where every app's
ExternalSecret reads from. Build it from `pass` so no value is ever typed or
shown:

```bash
kubectl create secret generic plausible-secrets -n rbx-ia-br \
  --from-literal=SECRET_KEY_BASE="$(pass rbx/plausible/secret_key_base)" \
  --from-literal=TOTP_VAULT_KEY="$(pass rbx/plausible/totp_vault_key)" \
  --from-literal=DATABASE_URL="postgres://plausible:$(pass rbx/plausible/postgres_password)@plausible-postgres:5432/plausible" \
  --from-literal=CLICKHOUSE_DATABASE_URL="http://plausible-clickhouse:8123/plausible_events"
```

The ExternalSecret in `apps/prod/plausible/externalsecret.yml` mirrors it into
the `plausible` namespace every 15 minutes.

### 3. DNS

The record is declared in `infra/terraform/dns/rbxsystems_ch.tf`
(`plausible_rbxsystems_ch`). Apply it with the usual wrapper:

```bash
cd infra/terraform/dns
../../../scripts/dns-tofu-env.sh tofu plan   # confirm only this record is added
../../../scripts/dns-tofu-env.sh tofu apply
dig +short plausible.rbxsystems.ch           # must return the ingress IP
```

The certificate cannot be issued before this resolves: the HTTP-01 challenge has
nowhere to land.

## Sync order

1. DNS resolves (step 3).
2. `plausible-secrets` exists in `rbx-ia-br` (step 2).
3. Postgres role and database exist (step 1).
4. Sync the ArgoCD app. ClickHouse comes up first; Plausible's first boot runs
   `createdb` plus every migration before it answers, which is why its liveness
   probe waits 180s.

```bash
kubectl -n plausible get pods -w
kubectl -n plausible logs deploy/plausible -f      # watch the migrations
kubectl -n plausible get certificate plausible-tls  # READY=True
curl -sI https://plausible.rbxsystems.ch | head -1  # 302 to /login
```

## First account

`DISABLE_REGISTRATION=invite_only` (upstream default) ships in `deploy.yml`.
Upstream does not document how the first account is created under each mode, so
do not assume: open `https://plausible.rbxsystems.ch/register` and try. If it
refuses, flip the env to `false`, sync, register the single admin account, then
set it to `true` and sync again. Keep that window short; the page is public.

After logging in, add two sites with exactly these domains, because they must
match what the tracker sends:

- `rbx.ia.br`
- `rbxsystems.ch`

Then confirm data flows: load each site, and the corresponding dashboard should
show the visit within seconds.

## Known gaps, deliberately out of the first deploy

- **No mailer.** `MAILER_ADAPTER` and friends are unset, so invites, password
  resets and email reports do not send. Postmark is already live in the fleet, so
  wiring `Bamboo.PostmarkAdapter` plus a `POSTMARK_API_KEY` key in the same
  Secret is a small follow-up. Until then the admin account's password is the
  only way in: keep it in `pass`.
- **No geolocation.** `IP_GEOLOCATION_DB` / `MAXMIND_*` are unset, so visits have
  no country breakdown. Adding it means a MaxMind licence key and a sidecar or
  init download into the existing `plausible-data` volume.
- **No backup of the event store.** ClickHouse data lives on a `local-path` PVC
  on jaguar: no replication, no volume expansion, and losing that disk loses the
  event history. Sites, users and settings survive on the external Postgres,
  which is backed up with the rest of that instance. Sizing was chosen up front
  (20Gi) because expansion is not available on this StorageClass.

## Rollback

```bash
# Remove the app; PVCs are retained (finalizer keeps them until deleted by hand)
kubectl -n argocd delete application plausible
```

The site keeps working without Plausible: the tracker request simply fails, which
is exactly the state before this bring-up. To also stop the failing request, unset
the `VITE_PLAUSIBLE_*` env in both frontend deployments.
