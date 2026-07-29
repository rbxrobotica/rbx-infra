# Runbook: Plausible CE

Self-hosted web analytics for `rbx.ia.br` and `rbxsystems.ch`, served from
`plausible.rbxsystems.ch`.

**Status: live since 2026-07-27.** Both sites registered, ingestion verified by
counting rows in ClickHouse. This is now the operations reference; the bring-up
steps are kept because they are the recovery procedure if the instance is rebuilt.

- Manifests: `apps/prod/plausible/`
- ArgoCD Application: `gitops/app-of-apps/plausible.yml` (manual sync)
- Upstream: `plausible/community-edition`, branch `v3.2.1`

## Architecture

| Piece | Where | Why |
| --- | --- | --- |
| Plausible app | Deployment, 1 replica, node `jaguar` | Single writer, node-local PVC |
| Postgres (sites, users, settings) | External `161.97.147.76:5432` | Postgres never runs inside k3s in production |
| ClickHouse (events) | StatefulSet in-cluster, node `jaguar`, 20Gi | The Postgres rule does not cover ClickHouse; Langfuse set the precedent |
| TLS | cert-manager `letsencrypt-prod` | Same as every other public host |
| Ingress | Traefik IngressRoute | Forwards `X-Forwarded-For` and the WebSocket upgrade the dashboard needs |

Both pods are pinned to `jaguar` with a toleration for
`robson.io/dedicated=analytics:NoSchedule`. jaguar has 24Gi RAM, 8 CPU and 394Gi
of disk and sits near-idle, while altaica, sumatrae and tiger run at 60-86%
memory. Pinning also keeps the Postgres traffic on the node that hosts Postgres.

This ClickHouse is **separate** from the Langfuse one: different image
(`clickhouse/clickhouse-server:24.12-alpine` against the chart's Bitnami build),
different node, and no ZooKeeper here because there is a single shard with no
replication. Do not point one app at the other's instance: the Langfuse one is
owned by its Helm chart and can be recreated on upgrade.

## Version pinning

Upstream publishes version **branches**, not git tags, and the image tag mirrors
the branch name. `v3.2.1` is current; material referencing `v3.0.1` is stale.
There is no image-updater on this app, so bump it deliberately.

## How the tracker is wired

This is the part that silently produced nothing for a day, so it is spelled out.

Plausible serves a **per-site** script, `pa-<id>.js`, which embeds the domain and
the event endpoint. The generic `/js/script.js` embeds neither and is useless
here. Each frontend deployment carries its own URL in `VITE_PLAUSIBLE_SCRIPT_SRC`:

```
rbx.ia.br      -> https://plausible.rbxsystems.ch/js/pa-RWUjVLP5jqiYl69hgg1FN.js
rbxsystems.ch  -> https://plausible.rbxsystems.ch/js/pa-YLjAxMQF6_-IvBjbWjVcf.js
```

The ids change only if a site is deleted and recreated in Plausible.

The script also **stays inert until `plausible.init()` is called**. The
`data-domain` attribute of the older snippet is not a bootstrap on this version:
the script loads, defines `window.plausible`, and sends nothing. The site calls
`init()` from `Analytics.svelte` via `bootstrapPlausible()`
(`rbx-systems-frontend`, `src/lib/analytics/index.ts`).

## Verifying ingestion

**`202 ok` from `/api/event` proves nothing.** Plausible answers 202 for events
it accepts and for events it discards. The only ground truth is the row count:

```bash
kubectl exec -n plausible plausible-clickhouse-0 -- \
  clickhouse-client -q "select count() from plausible_events.events_v2"
```

Two guards silently discard traffic from any automated check, and both cost real
debugging time if forgotten:

1. **Client side:** the tracker drops the event when `navigator.webdriver` is
   set, unless `window.__plausible` is truthy. In Playwright, set it before
   navigating: `await page.addInitScript(() => { window.__plausible = true })`.
2. **Server side:** a `HeadlessChrome` User-Agent is classified as a bot and
   dropped *after* answering 202. Override the UA with a normal Chrome string.

A visit from a real browser needs neither. Treat the dashboard's "Verify Script
Installation" button as secondary to the row count.

## Rebuild procedure

Steps 1 to 3 involve credentials or DNS.

### 1. Postgres role and database

On the external instance, as a superuser. Keep the password in `pass` under
`rbx/plausible/postgres-password`; never echo it.

```sql
CREATE ROLE plausible LOGIN CREATEDB PASSWORD '<pw>';
CREATE DATABASE plausible OWNER plausible;
```

`CREATEDB` is required: the start command runs `/entrypoint.sh db createdb` on
every boot, idempotently.

**`pg_hba.conf` needs two entries**, and this is not optional. The pod runs on
jaguar and connects to jaguar's own address, so the traffic is not SNAT'd and
arrives with a **pod IP**. No public-IP `/32` entry can ever match it:

```
host plausible plausible 10.42.1.0/24 scram-sha-256
host postgres  plausible 10.42.1.0/24 scram-sha-256
```

The second line covers the maintenance database `createdb` connects to in order
to check whether the target exists. The role is not superuser and cannot reach
any other database. Reload with `select pg_reload_conf()`.

Testing the login from the host itself (`psql -h 127.0.0.1`) matches a different
`pg_hba` line and passes while the pod still fails. Test from the pod, or read
the pod logs, which name the rejected source IP.

### 2. Secrets

```bash
openssl rand -base64 48 | pass insert -m rbx/plausible/secret-key-base
openssl rand -base64 32 | pass insert -m rbx/plausible/totp-vault-key
pass insert rbx/plausible/postgres-password
```

`SECRET_KEY_BASE` must be at least 64 bytes; 48 raw bytes base64-encoded is 64
characters, which satisfies it.

Build the source Secret in `rbx-ia-br` straight from `pass`, so no value is ever
typed or displayed:

```bash
kubectl create secret generic plausible-secrets -n rbx-ia-br \
  --from-literal=SECRET_KEY_BASE="$(pass rbx/plausible/secret-key-base)" \
  --from-literal=TOTP_VAULT_KEY="$(pass rbx/plausible/totp-vault-key)" \
  --from-literal=DATABASE_URL="postgres://plausible:$(pass rbx/plausible/postgres-password)@plausible-postgres:5432/plausible" \
  --from-literal=CLICKHOUSE_DATABASE_URL="http://plausible-clickhouse:8123/plausible_events"
```

The ExternalSecret mirrors it into the `plausible` namespace every 15 minutes.

`POSTMARK_API_KEY` is deliberately **not** in this Secret. It is read from
`rbx-ia-br/rbx-contact-secrets`, key `POSTMARK_SERVER_TOKEN`, which the contact
system already owns, so the Postmark server token has one home in the cluster
and one place to rotate. Nothing to create here during a rebuild; if that Secret
is missing, follow the contact system's own bring-up first.

### 2b. Mailer

Postmark, on the existing **RBX Institutional** server (id 19089132, delivery
type Live) rather than a new one. Password resets, invites and email reports go
out as `no-reply@rbxsystems.ch`.

| Variable | Value | Where |
| --- | --- | --- |
| `MAILER_ADAPTER` | `Bamboo.PostmarkAdapter` | plain env, `deploy.yml` |
| `MAILER_EMAIL` | `no-reply@rbxsystems.ch` | plain env, `deploy.yml` |
| `MAILER_NAME` | `RBX Analytics` | plain env, `deploy.yml` |
| `POSTMARK_API_KEY` | Postmark server token | Secret, from `rbx-contact-secrets` |

Two things about the sender that are easy to get wrong:

- **`rbxsystems.ch` is verified in Postmark as a domain**, so any local part on
  it is accepted, including one that was never registered as a signature.
- **`tx.rbxsystems.ch` is not.** Sending from it returns HTTP 422 with
  `ErrorCode 400`, "not a Sender Signature on your account", and nothing is
  delivered. `docs/PLAN-dns-email-architecture.md` reserves that subdomain for
  transactional mail and its SPF record exists, but the Postmark side was never
  completed. Moving Plausible there means verifying the domain in Postmark
  first, not just editing `MAILER_EMAIL`.

Probe a candidate sender before trusting it. This sends a real message when the
address *is* accepted, so aim it at an inbox you own:

```bash
TOKEN=$(kubectl get secret rbx-contact-secrets -n rbx-ia-br \
  -o jsonpath='{.data.POSTMARK_SERVER_TOKEN}' | base64 -d | tr -d '[:space:]')
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://api.postmarkapp.com/email \
  -H "X-Postmark-Server-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"From":"no-reply@rbxsystems.ch","To":"ceo@rbxsystems.ch",
       "Subject":"probe","TextBody":"probe","MessageStream":"outbound"}'
```

`200` means accepted, `422` means the sender is not usable. Note the trailing
`tr -d '[:space:]'`: `base64 -d` leaves a newline that Postmark rejects as an
invalid token, and the resulting `401` reads exactly like a rotated credential.

To confirm delivery end to end after a deploy, use the app rather than the API:
`/password-reset` with a known account address, then check the message in
Postmark's Activity view or in the destination inbox.

### 3. DNS

The record is declared in `infra/terraform/dns/rbxsystems_ch.tf`. Apply it, then
verify that **both** nameservers answer before expecting a certificate.

```bash
cd infra/terraform/dns
../../../scripts/dns-tofu-env.sh tofu plan     # confirm what is pending
../../../scripts/dns-tofu-env.sh tofu apply -target=powerdns_record.plausible_rbxsystems_ch
dig +short plausible.rbxsystems.ch @149.102.139.33
dig +short plausible.rbxsystems.ch @167.86.92.97    # must also answer
```

Replication is automatic since 2026-07-28, when `SOA-EDIT-API` was set on all
four zones; allow up to a minute. Before that the serial had to be bumped by
hand or the secondary never received the record, which is what made this
bring-up fail its first certificate. If the second `dig` stays empty, see
`docs/runbooks/DNS-TROUBLESHOOTING.md` §5.

Use `-target` when the plan shows unrelated pending records: the zone files can
carry records declared by earlier work that were never applied. The certificate
cannot issue until **both** nameservers answer.

### 4. Sync

The namespace must be allowlisted in `gitops/projects/rbx-applications.yaml`
before the first sync, or it fails with `InvalidSpecError` and creates nothing.

```bash
kubectl -n plausible get pods -w
kubectl -n plausible logs deploy/plausible -f       # createdb + migrations
kubectl -n plausible get certificate plausible-tls  # READY=True
curl -sI https://plausible.rbxsystems.ch/ | head -1 # 302
```

Traefik drops the **whole** IngressRoute while the TLS secret is missing, so
until the certificate issues the host answers 404 on both ports. That 404 is a
symptom of the certificate, not of the route.

### 5. First account and sites

Registration is `invite_only` and the first account is allowed: open `/register`.
Then add two sites with exactly these domains, because they must match what the
tracker reports:

- `rbx.ia.br`
- `rbxsystems.ch`

Both use timezone `America/Sao_Paulo`: the reader is in Brazil, and different
timezones would shift day boundaries and make the two dashboards
non-comparable. Plausible stores UTC, so this is a display setting and can be
changed later without data loss.

`www.rbxsystems.ch` needs no separate site: it serves the same HTML with
`data-domain="rbxsystems.ch"`.

## Retention: 14 months, enforced by ClickHouse TTL

Applied 2026-07-29, from the retention the growth taxonomy declares
(`rbx-growth`, `marketing/2026-h2-growth/analytics/event-taxonomy.yaml`).
Plausible CE has no retention setting of its own and keeps data forever.

```sql
ALTER TABLE plausible_events.events_v2   MODIFY TTL timestamp + INTERVAL 14 MONTH;
ALTER TABLE plausible_events.sessions_v2 MODIFY TTL start     + INTERVAL 14 MONTH;
```

TTL rather than a pruning CronJob: both tables are partitioned by month
(`toYYYYMM`), so ClickHouse drops whole partitions during merges instead of
rewriting data, and there is no extra workload that can fail silently.

**This is local configuration, not upstream.** The tables belong to Plausible,
which already has 51 migrations applied and will keep migrating on upgrade. A
migration that recreates either table drops the TTL with it, and nothing warns
you: the instance keeps working and the retention you believe you have is gone.
**Check after every Plausible upgrade:**

```bash
kubectl exec -n plausible plausible-clickhouse-0 -- clickhouse-client -q \
  "select name, extract(engine_full, 'TTL [^S]*') from system.tables \
   where database='plausible_events' and name in ('events_v2','sessions_v2')"
```

Empty output for either table means retention is off and the ALTERs above have
to be reapplied.

## Known gaps

- **Transactional mail rides the institutional Postmark server.** Plausible
  shares a server, a token and a reputation with the contact system. A bounce
  storm on one is felt by the other, and the token cannot be revoked for one
  without cutting the other. The split is designed
  (`docs/PLAN-dns-email-architecture.md`, RBX Transactional on
  `tx.rbxsystems.ch`) but not built; see §2b for what it needs.
- **No geolocation.** No country breakdown; needs a MaxMind licence.
- **No backup of the event store.** ClickHouse data lives on a `local-path` PVC
  on jaguar: no replication, and this StorageClass has **no volume expansion**.
  Losing that disk loses the event history; sites, users and settings survive on
  the external Postgres. 20Gi was chosen up front for that reason, and growing
  it means recreating the PVC.
- **`http://` answers 404** instead of redirecting to HTTPS. The IngressRoute
  declares both entrypoints and the higher-priority route wins on `web` too.
  `cms.rbxsystems.ch` shares the pattern and the behaviour. HTTPS is unaffected.

## Rollback

```bash
kubectl -n argocd delete application plausible
```

PVCs are retained. The sites keep working without Plausible: the tracker request
simply fails, which is the state before this bring-up. To also stop that request,
unset the `VITE_PLAUSIBLE_*` env in both frontend deployments.
