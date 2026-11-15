# Historical Thalamus restore evidence

Event date: 2026-07-18. Curated on 2026-10-29 from the source identified in the
[manifest](thinkcentre-backup-sources-2026-10-29.json). No restore was executed
as part of this curation; the raw dump, globals and private logs were not copied.

## Scope and reported observations

The source reports reading `thalamus.dump` and globals from Contabo S3 stamp
`2026-07-18T011501Z`, matching source/download byte counts, and restoring in an
ephemeral PostgreSQL 16 container on the operator workstation, offline and
without published ports. Production PostgreSQL was only queried read-only.

The source preserves two attempts:

1. Without the required globals/roles, restore exited 1 with seventeen ACL-related
   errors. This attempt remains a failure.
2. After globals were restored, rerunning the dump restore exited 0, with an
   empty restore log. The source reports fourteen tables, ninety-six TOC entries
   and two schema-migration rows, and labels this isolated attempt PASS.

The source also compares selected restored counts with later live counts.
Those observations are consistent with intervening writes but are not equality
checks against a simultaneously captured source. No rows or role definitions
are reproduced here.

## Limits

This supports only the historical isolated Thalamus test. It does not establish
ZITADEL or all-database recovery, cross-database consistency, PITR, restoration of
the production Jaguar host, independently held keys, a different provider/account
or current backup cadence. Full observed RPO/RTO and an accountable executor
identity were not established for a schema-complete Resilience execution record
by this curation. Do not infer them from container readiness or file timestamps.

The original source's broad full-server wording is narrowed here to the one
database actually restored. See [logical backup history](../../runbooks/JAGUAR-LOGICAL-BACKUP-HISTORY.md)
and the separate [etcd evidence](2026-07-18-etcd-restore.md).
