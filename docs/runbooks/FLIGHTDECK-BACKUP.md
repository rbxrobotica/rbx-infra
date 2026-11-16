# FlightDeck PostgreSQL preservation and recovery

Owner: Leandro, Merovelis. Source database: `flightdeck`, PostgreSQL 16 on
jaguar. This application database is independent of the Plausible databases.
A Plausible receipt does not prove FlightDeck recovery.

## Observed recovery point, 2026-11-16

Authenticated read-only inspection found PostgreSQL 16.15, `archive_mode=off`,
`archive_timeout=0`, no pgBackRest installation/configuration and no pgBackRest
timers. ADR-0012 documents a target, not an operating backup. Do not enable WAL
archiving by restarting the shared database as part of this change.

An owner-host operation obtained a bounded custom-format, no-owner/no-privilege
`pg_dump` in a read-only transaction. Plaintext remained in process memory.
The application archive was encrypted to the existing owner OpenPGP recipient,
uploaded below `flightdeck/backups/full/` in private versioned S3, independently
downloaded by exact version and checked by SHA-256. The downloaded archive was
decrypted on the owner's host and restored into PostgreSQL 16.15 in a disposable
container with no network and tmpfs storage. The private key never entered the
container or source cluster.

The restored database contained migrations 001 through 003, two actors, one
Workspace, three Workstreams, 30 audit events, two audit immutability triggers
and no unvalidated constraints. `pg_restore --exit-on-error` completed; measured
restore and inventory time was 0.703 seconds, excluding snapshot and transfer.
No SQL was written to production. The disposable restore container was stopped.

Private evidence and encrypted owner-host copies:
`~/.local/share/rbx-backups/flightdeck/20261116T032554Z.*` and
`~/rbx/artifacts/flightdeck-connector-recovery-20261116/`.
The restore proof SHA-256 is recorded in the private preflight report.

## Recurring backup contract

The admitted `scripts/flightdeck/backup_runner.py` entry point reuses the tested
PostgreSQL runner, hard-binds database `flightdeck`, and selects only the
`flightdeck/backups/` collection. It requires an explicit S3 destination and
credentials, the public recipient key and PostgreSQL 16 clients. It writes no
SQL, needs no Kubernetes token and never deletes backups. Other entry points
remain bound to their original databases and prefixes.

A new backup image must pass all runner, archive, secret and vulnerability
checks before main-only publication. Pin a verified tag and digest in a separate
GitOps change, validate pull access, and then reconcile only the backup resources.
Do not publish a placeholder image or activate a CronJob against an unverified
image. A successful explicit Job is not evidence of a scheduler-created Job.

Target schedule: 03:45 America/Sao_Paulo, with no concurrent execution and a
26-hour overdue alert. RPO target: 24 hours, no PITR guarantee. RTO target: four
hours; the small isolated test above does not establish end-to-end recovery RTO.
Staging must be bounded RAM, not unencrypted node disk. No private key in cluster.
Retention: preserve all existing versions; no deletion or retention changes.
The recurring configuration and its first scheduled execution are not yet
verified by this source change.

S3 is outside the database host and cluster but shares the Contabo account.
Keep exact-version, verified encrypted copies on the owner's separate host;
do not claim S3 alone provides an independent account failure boundary.
Automated independent custody and monitored restore age must be verified before
calling recovery fully operational.

## Recovery procedure

1. Obtain existing S3 credentials from `pass` in process memory. Do not print
   values, connection strings, Kubernetes Secret contents or raw upstream errors.
2. Run `scripts/flightdeck/s3_archive.py latest --artifact-kind full --lookback-days 3`.
   Verify age against 26 hours. Metadata alone is not download or recovery proof.
3. Download the exact returned version with `download KEY --version-id VERSION
   --expected-sha256 SHA --output PRIVATE_NEW_FILE.gpg`. Preserve ciphertext.
4. Decrypt on the owner's host. Validate archive members, database identity and
   inner dump hash. Keep plaintext in RAM or an explicitly encrypted filesystem.
5. Restore using `pg_restore --exit-on-error --no-owner --no-privileges` into a
   new disposable PostgreSQL 16 instance with no production network access.
   Never use production or a shared test database. Check migration inventory,
   constraints, audit and ledger records, grants and capture/review provenance.
6. Preserve a timestamped restore proof and archive it encrypted in the
   `flightdeck/backups/restore-proof/` collection with exact-version readback.
   A receipt that says `restore_tested_at: null` is not a tested restore.
7. Repeat the isolated test when the last proof is 28 days old. Escalate at
   35 days. Reconcile drift through IaC; never restore over production to test.
