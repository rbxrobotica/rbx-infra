# IRC PostgreSQL destination preflight

Assessment date: 2026-10-12, America/Sao_Paulo.
Readiness for live migration and human-query activation: **BLOCKED**.

This is a repository-only assessment of the PostgreSQL destination for the IRC
adapter's durable queue, supplemented by the isolated tests described below.
It approves neither a host nor a cutover. No deployed database connections, SSH
commands, secret reads, production writes or service changes were performed.
Keep the deployed adapter and its existing state unchanged while the live
evidence below is collected.

## Declared standard and evidence

The source baseline is `rbx-infra` commit
`03a99d8201542624fdf32c1b73a1cd070f6c906b`.

| Evidence | What it establishes | What it does not establish |
|---|---|---|
| [Database architecture](../../../docs/infra/DATABASE.md) | ParadeDB/PostgreSQL on the declared shared host `jaguar`, dedicated database and role per application, no application database StatefulSets in k3s | Selection, existence or readiness of an IRC database |
| [ParadeDB role](../../../bootstrap/ansible/roles/paradedb/tasks/main.yml) | Ansible database/role/grant provisioning patterns; the role explicitly does not install ParadeDB | An IRC provisioning declaration or sufficient privileges for the new adapter |
| [External DB recovery](../../../docs/runbooks/EXTERNAL-POSTGRES-APP-DB-RECOVERY.md) | Checks for app-specific authentication, HBA rules and selectorless Kubernetes Services | A workstation-to-database route or verified TLS identity |
| [ADR-0012](../../../docs/adr/ADR-0012-pgbackrest-cluster-backup-jaguar.md), [defaults](../../../bootstrap/ansible/roles/pgbackrest/defaults/main.yml) and [runbook](../../../docs/runbooks/PGBACKREST-BACKUP.md) | Planned cluster WAL archiving, encrypted backup repositories, local retention of 2 full backups and off-site retention of 4 | Current backup health, off-site deployment or a successful restore of the selected IRC state |
| [Comms preservation](../../../docs/runbooks/COMMS-PRESERVATION.md) | Recorded isolated PostgreSQL restore on 2026-10-05 for the separate `rbx_comms` database | Coverage of another database, current cluster-wide PITR or automatic inclusion of a new IRC database |
| [Resilience record at the inspected revision](https://github.com/rbxrobotica/rbx-resilience/blob/bd1e703456d3c769c3c40eeeb7248fd9f3fbed5a/components/postgres-zitadel.yaml) | The inspected cluster-related record still contains placeholders and an empty drill list | Absence of all other recovery evidence or the current live state |

The pgBackRest defaults set `pgbackrest_offsite_enabled: false`; no enabled
inventory override was found in this assessment. The runbook identifies a
monitoring gap pending integration with observability. These repository facts
are not a fresh observation of the server. `archive_timeout = 60s` is a declared
setting, not a measured RPO. A local backup on the database host does not cover
loss of that host or its provider account.

## Isolated implementation evidence

The [store tests](../bots/strategos-irc-bot/tests/test_postgres_store.py),
[migration tests](../bots/strategos-irc-bot/tests/test_mailbox_migration.py),
[administrative CLI tests](../bots/strategos-irc-bot/tests/test_storage_cli.py)
and [transport tests](../bots/strategos-irc-bot/tests/test_postgres_transport.py)
passed against disposable PostgreSQL databases reached through a numeric
loopback address on 2026-10-12. The fixture uses synthetic records and removes
only its own randomly named databases.

Coverage includes preserved source fields and IDs, heartbeat metadata,
concurrent claims/imports, rollback, uncertain commit handling, migration hold,
restricted runtime permissions, daemon ownership and socket closure after a
database connection failure. The lost commit acknowledgement is an injected
failure around a real committed transaction. The IRC peer is a local protocol
fixture. These checks do not establish production backup health, a real
mailbox migration, remote TLS reachability or continued Halloy operation.

## Destination contract

1. Record the selected PostgreSQL service, DNS identity, server version,
   network boundary and accountable operator before provisioning. `jaguar` is
   the declared standard host, not an automatically selected live endpoint.
   This workload requires PostgreSQL transactions, not ParadeDB search features.
2. Allocate a dedicated database and application role for each logical adapter
   mailbox identity. Processes serving the same adapter use the same database;
   separate adapters do not share that mailbox or reuse `rbx_comms` credentials.
   Record the schema, adapter account/channel/server identity, migration owner
   and runtime role. Proposed names remain unassigned until reviewed.
3. Use a private route with verified TLS server identity for remote access.
   Record the trusted CA reference and matching hostname; never disable
   certificate validation to pass a probe. Restrict firewall and HBA rules to
   the selected database, role and necessary source identities. Do not expose
   PostgreSQL broadly to accommodate a workstation. A private network alone
   does not prove TLS identity, database authorization or backup coverage.
4. Keep connection credentials in the approved secret store. Record references
   and delivery mechanism only. A service environment may materialize a secret,
   but it is not its source of truth. Verify that the actual service and CLI
   receive the same intended target; never print a DSN, password, environment
   dump or connection error containing credentials.
5. Verify `CONNECT`, schema `USAGE`, table DML and sequence rights against the
   implementation. Record any required schema creation or DDL separately;
   runtime credentials must not have superuser, role-creation, replication or
   cross-application rights. If schema initialization uses the runtime role,
   its DDL scope must remain inside this dedicated database. Document default
   privileges for future tables and prove that unrelated databases are denied.
6. Bound connection count, acquisition timeout, statement timeout and lock
   timeout for the adapter, CLI and supervision processes together. Record the
   server's capacity allowance. The daemon ownership lock requires a direct
   connection or a pooler in session mode. Transaction and statement pooling
   are unsupported because ownership must stay on the same PostgreSQL session.
   Validate the selected route; a direct connection test does not validate a
   separate pooler. Record clock synchronization health and measured offsets
   for adapter, consumer and IRC server hosts. Freshness, sessions and leases
   still use application clocks; adding PostgreSQL does not move deadlines to
   database time. Unknown or unhealthy synchronization blocks acceptance.
7. A PostgreSQL outage must fail admission and completion closed. Do not fall
   back to SQLite, a second database, an alternate host or a second consumer.
   Do not automatically repeat work whose commit or external execution result
   is uncertain. Remote storage does not keep a stopped local executor running.

The existing [bootstrap recovery script](../../../bootstrap/scripts/bootstrap-external-postgres-app-db.sh)
reads an existing Kubernetes Secret even with `--dry-run`; its mutating path
aligns credentials, grants privileges, adds `host` HBA entries and reloads the
server. It is not an IRC installer, a TLS enforcement check or an appropriate
workstation command without a reviewed target-specific provision plan.

## Evidence required before cutover

| Check | Required evidence | Current result |
|---|---|---|
| Target and custody | Selected service, dedicated DB/role/schema, owner, secret reference, IaC revision | Unknown |
| Network and TLS | Probe from the actual adapter host; verified CA/hostname; restricted HBA/firewall; denied unauthorized route | Unknown |
| Authentication and permissions | Sanitized effective DB/user/schema/version, required grants, denied cross-app access and agreed connection budget | Unknown |
| Clock synchronization | Dated synchronization health and measured offsets compatible with configured IRC freshness, session and lease limits | Unknown |
| Source preservation | Quiesced source, consistent backup, encrypted remote receipt, integrity check and isolated restore result | Unknown |
| Destination preservation | Backup coverage including the selected DB, last successful archive, retention, encryption/key recovery, independent failure domain and freshness alert | Unknown |
| Recovery | Isolated restore/PITR evidence, target and observed RPO/RTO, complete role/schema recovery and executable owner runbook | Unknown |
| Semantic migration | Source/destination table and row inventory, IDs, deduplication, claims/fences, response frames and uncertain states preserved without dispatch | Synthetic checks passed; actual mailbox rehearsal pending |
| Return path | One authoritative writer, preserved pre-cutover source, bounded maintenance window and reconciliation of any post-cutover writes | Synthetic ownership/hold checks passed; actual return rehearsal pending |

Start with metadata-only inspection through the approved application path.
Exercise DDL, writes, competing claims, outages and restore only in an isolated
destination with synthetic state. Real records and delivery workers must stay
out of that rehearsal. A successful connection is only the authentication and
routing check; it does not close the backup, permission or recovery gates.

For real migration, stop admission and quiesce every writer before the final
snapshot. Keep all consumers and outbound publishers stopped during import and
verification. Restoring an older snapshot can resurrect an already attempted
response; reconcile delivery uncertainty before reopening admission. An old
SQLite snapshot is not a safe automatic rollback after PostgreSQL has accepted
new writes. Preserve both states and reconcile under the return plan.

## State-policy report

| Field | Repository-only result |
|---|---|
| State changed | Implementation/documentation and disposable synthetic test state; no deployed state or data changed |
| Source of truth | Existing adapter store before cutover; this repository for implementation/IaC; approved secret store for credentials |
| Remote backend | PostgreSQL/ParadeDB required; exact IRC destination unselected and unverified |
| Backup and last execution | IRC source and destination evidence unknown; no inherited claim from the separate Comms backup |
| Last tested restore | No IRC destination restore evidence established by this assessment |
| RPO/RTO | Targets and observed values unknown; operator must define and measure them |
| IaC/commit | Source assessment baseline above; IRC destination provisioning remains pending |
| Drift found | No live comparison performed; runtime drift unknown |
| Residual local state | Existing local mailbox has not been inspected or modified; its independent recovery copy is unverified |
| Risks or blockers | Destination, credentials delivery, private TLS route, permissions, backup/restore, migration rehearsal and return plan remain open |

Record dated evidence links and results in the reviewed cutover record before
changing readiness. Do not replace unknown fields with assumptions based on
merged code, generated configuration or another application's successful drill.
