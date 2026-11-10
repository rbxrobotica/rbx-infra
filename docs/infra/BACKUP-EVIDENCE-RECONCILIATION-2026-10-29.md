# Backup evidence reconciliation

Evidence cut: 2026-10-29, America/Sao_Paulo. This is documentary reconciliation,
not a new backup, restore, procurement or deployment result.

## Decisions and observations

| Date | Source | What it establishes | What it does not establish |
| --- | --- | --- | --- |
| 2026-05-07 | [Mail ADR-0004](../adr/ADR-0004-mail-backup-target-rsync-net-zurich.md) | Historical selection of rsync.net Standard Zurich for Mailcow | Account creation, purchase, installed backup or successful restore |
| 2026-06-22 | [Kine incident](../incidents/INCIDENT-2026-06-22-KINE-BLOAT.md) and source `gov094.md` | A previously announced Jaguar copy was subsequently found truncated; a later consistent copy and compaction were reported | Integrity merely because a copy exists; the transcript's claim that rsync.net was already used for mail |
| 2026-06-24 | [HA migration](../runbooks/K3S-HA-MIGRATION.md), ADR-0004 scope clarification | Embedded-etcd migration and snapshot sync to Jaguar were documented separately from paid off-site mail backup | Independent provider/account protection or full cluster recovery |
| 2026-07-17/18 | [Logical backup history](../runbooks/JAGUAR-LOGICAL-BACKUP-HISTORY.md) | Scheduled dumps to Contabo S3 were reported; one Thalamus restore was subsequently recorded as passing in isolation | Continuous operation through August/October, all-database restore, WAL/PITR or an independent provider/account |
| 2026-07-18 | [Etcd historical evidence](evidence/2026-07-18-etcd-restore.md) | An isolated one-member etcd restore, integrity and resource-count checks were reported | A production quorum rebuild, workload/PVC recovery, or current recovery readiness |
| 2026-08-31 | [pgBackRest ADR](../adr/ADR-0012-pgbackrest-cluster-backup-jaguar.md) | Accepted direction for physical cluster backup and WAL; historical Zurich destination extended from mail | That the role, WAL archive, account or off-site repository was active |
| 2026-08-31 | Operator-attested commercial correspondence | The vendor told the operator that Zurich had been withdrawn for new contracts and offered Denver or Silicon Valley | A purchase, acceptance of another region, or availability for a later order |
| 2026-10-12 | [Readiness](../runbooks/RSYNC-NET-READINESS.md) | The operator confirmed no rsync.net account was available | That the October web-page claim about Zurich overrides the commercial response |
| 2026-10-12 | [Jaguar preflight](../../infra/irc/docs/POSTGRES-LIVE-PREFLIGHT-2026-10-12.md) | No pgBackRest executable/repository was found and WAL archiving was off in that assessment; crontab reads were inconclusive | Absence of every application backup or a recheck of the July logical-dump pipeline |
| 2026-10-05/18 | [Comms preservation](../runbooks/COMMS-PRESERVATION.md), [reliability execution](RBX-INFRA-RELIABILITY-EXECUTION-2026-10-18.md) | Separate, scoped application-restore and infrastructure measurements | Fleet-wide recovery, attachment-blob preservation or N-1 acceptance |

The commercial statement above is the operator's supplied account of a message
read in the originating session. This review did not reopen or export email.
No provider alternative is selected here. The historical decisions and their
trade-offs remain visible; changed geography or procurement needs its own owner
decision and review.

## How to use historical evidence

The [source manifest](evidence/thinkcentre-backup-sources-2026-10-29.json) records
paths, complete SHA-256 values and excerpt ranges. Hashes identify the documents,
not the underlying backup payloads. The curated reports preserve outcomes and
limitations without transcripts, application rows, configuration or credentials.
They are retrospective summaries, not newly executed drills.

The original etcd report still called the PostgreSQL drill pending. The separate
PostgreSQL report of the same date records a later-described successful rerun.
Keep both observations and their source identities; file modification times do
not independently establish the execution sequence. Use the reconciled
[Thalamus report](evidence/2026-07-18-jaguar-thalamus-restore.md) for that scoped
historical result, without editing a failed attempt into a pass.

## Ownership and remaining work

- Infra owns infrastructure decisions, deployment/runbook sources and the
  distinction between intended, installed and observed behavior.
- `rbx-resilience` owns component inventory, dependencies, recovery procedures
  and drill evidence. Historical reports here cannot satisfy missing execution
  fields or create a current `fact` there. Its 2026-08-27 auditor trust boundary
  still applies.
- `rbx-agent-layer` owns reusable instructions; `rbx-contexts` supplies small
  source-linked handoffs. Neither should embed an undated operational snapshot.
- `rbx-memory` is the operational memory runtime and single writer to the
  `rbx-memory` source bucket. Its search index is rebuildable; the source objects,
  approved RAG packages, schema, access custody and offline recovery instructions
  need their own preservation inventory. `rbx-governance` owns schema/policies.
- `rbx-codex-memory` preserves private conversation captures in a separate vault.
  Its CLI name does not make it the operational Memory service. Capture coverage
  does not automatically include arbitrary files under `~/docs`.

The proposed [cross-provider pilot, Resilience #7](https://github.com/rbxrobotica/rbx-resilience/pull/7)
is separate pending work, not an adopted replacement for these ADRs or a recovery
result. Independent custody and access must be demonstrated before any recovery
capability is asserted. Instructions and keys required to recover Memory cannot
be obtainable only through Memory, RBX SSO/DNS or the protected host.

Before future activation, establish the actual source inventory and consistent
capture, reconcile July counts/timestamps, measure coverage and usable-backup
age, select and authorize the destination, verify key custody and isolation,
then perform an authorized isolated restore with observed RPO/RTO and failures
recorded. No such activation is performed by this reconciliation.
