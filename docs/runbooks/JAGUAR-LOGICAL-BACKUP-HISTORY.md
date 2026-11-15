# Jaguar logical PostgreSQL backup history

Historical record: 2026-07-17/18, reconciled on 2026-10-29. This is not an active
deployment recipe or evidence that the schedule still runs.

## Reported capture

The source `thinkcentre:/home/psyctl/docs/jaguar-pg-backup-s3.md` describes a
host-deployed script, daily cron declared as 03:15 UTC, globals plus custom-format
per-database dumps, two-day local staging and fourteen-day remote retention in
the private Contabo S3 bucket `rbx-backups`, under `jaguar/postgres/`.
It says the script was not yet versioned and payloads were not client-side
encrypted. These are historical declarations, not checks made on Jaguar during
this reconciliation.

The upload-only record reports stamp `2026-07-17T181503Z`, eighteen files,
seventeen database dumps plus globals. Another part says sixteen databases.
The restore report identifies stamp `2026-07-18T011501Z`. Reconcile database
inventory, the declared cron timezone, capture times and stamps before relying
on a schedule or calculating observed RPO. An object count alone is insufficient.

Contabo S3 provides a remote copy in the reported design; that does not establish
the different-provider-and-account off-site boundary required by Resilience.
The fourteen-day retention described in July does not show that the July payload
still exists in October. No payload was retrieved in this reconciliation.

## Reported recovery

- The 2026-07-17 test was initially claimed successful, then retracted because
  verification failed and no successful output had been captured.
- The [2026-07-18 Thalamus evidence](../infra/evidence/2026-07-18-jaguar-thalamus-restore.md)
  describes an isolated PostgreSQL 16 container, globals then one database dump,
  and a passing verification. It does not restore every Jaguar database.
- Missing roles caused a failed attempt before globals were restored. Preserve
  that failure alongside the subsequent successful attempt.
- The original staging directory was root-only. Any future procedure must use
  an explicitly isolated destination with appropriate permissions, not copy the
  historical scratch commands onto the live host.
- A ZITADEL database dump alone does not recover its external master key or
  independently restore identity service behavior.

## Relationship to pgBackRest

[ADR-0012](../adr/ADR-0012-pgbackrest-cluster-backup-jaguar.md) adds physical
cluster backup/WAL as the accepted direction. Historical logical dumps do not
prove WAL or PITR, and a physical-backup proposal does not erase their existence.
The [2026-10-12 preflight](../../infra/irc/docs/POSTGRES-LIVE-PREFLIGHT-2026-10-12.md)
found pgBackRest/WAL inactive but did not establish absence of all logical
backups. Current schedule, coverage, encryption, retention and restore remain
unverified in this record.

See the [source manifest](../infra/evidence/thinkcentre-backup-sources-2026-10-29.json)
for document hashes and the [reconciliation](../infra/BACKUP-EVIDENCE-RECONCILIATION-2026-10-29.md)
for procurement and recovery boundaries.
