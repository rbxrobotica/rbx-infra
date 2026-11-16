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

## Reviewed cluster configuration

The backup image source was merged as PR 402, commit
`b3ddae8538ab9bc82a0550fe914444e6f0d099cc`. Main publication run 43799979487
passed its tests and image scan. Anonymous registry manifest/config access was
verified for `ghcr.io/rbxrobotica/comms-backup:sha-b3ddae8` with digest
`sha256:3f5ddf6636672be688670d9fe9361697cd73a24775c264f02a30e51f96297248`.
The shared artifact contains a dedicated FlightDeck entry point; existing
application CronJobs retain their own image pins and scopes.

The GitOps backup resources use that exact image, bounded RAM staging, a public
recipient ConfigMap, no Kubernetes token, and the existing application database
credential plus the existing separate S3 backup credential. Source RBAC must be
reconciled first, then only the backup ServiceAccount, ConfigMap, ExternalSecret,
CronJob and PrometheusRule. Do not sync or prune the application database or PVCs
as part of backup activation. Preserve the existing application Secret.

## Independent owner custody

`scripts/flightdeck/install_owner_copy.py` plans the owner-host service and
installs it with `--apply` after its source is reviewed. The daily timer runs at
04:15 America/Sao_Paulo, after the cluster backup, with persistent catch-up.
It does not produce the primary backup: the Kubernetes CronJob does that even
when this desktop is off. The owner service downloads a new exact-version,
hash-verified ciphertext copy and retains a private receipt. It does not decrypt,
claim restoration, delete old files or change retention. Existing S3 credentials
are read from pass in process memory. Source hashes and commit are retained.

This copy is outside the source host, cluster and Contabo account storage.
Its cadence depends on the owner host being available and the existing
password-store access. Check the user service result and last private copy
receipt during Sunday review; an unavailable host or credential is a gap,
not zero data or proof that independent custody is current.

## Executable isolated restore check

Use `scripts/flightdeck/restore_check.py` (also installed with the owner tools).
It requires the cluster-produced receipt and its independently downloaded
ciphertext. The receipt's manifest hash, exact archive version and ciphertext
hash are checked before decryption. Decryption is bounded on the owner host;
archive members, source database/version and inner dump hash are checked before
loading. The target must be a fresh dedicated local container with no network,
no host bind/persistent mounts and tmpfs PostgreSQL data. Its immutable container
ID is pinned after inspection. Existing databases are rejected.

Start a disposable PostgreSQL16 instance in a separate terminal with the normal
build lease. The fixture password below is public and local-only:

```sh
rbx-build-cache podman --rm --name flightdeck-recovery-proof-TIMESTAMP \
  --network none --memory 768m --cpus 1 \
  --tmpfs /var/lib/postgresql/data:rw,size=512m \
  --env POSTGRES_USER=restore_owner --env POSTGRES_DB=flightdeck_restore \
  --env POSTGRES_PASSWORD=disposable_restore_only -- \
  docker.io/library/postgres@sha256:efdf07c2f9d4df592783dcc8ea5f6db02efbf5f6452b527225ff5e58364570e9
python3 scripts/flightdeck/restore_check.py --archive PRIVATE_FILE.tar.gpg \
  --receipt PRIVATE_CLUSTER_RECEIPT.json --container flightdeck-recovery-proof-TIMESTAMP \
  --proof PRIVATE_NEW_PROOF.restore-proof.json
podman stop flightdeck-recovery-proof-TIMESTAMP
```

Replace TIMESTAMP with a unique lower-case identifier and use a private proof
directory. No production URL is an accepted restore argument. Stop the fixture
after proof is saved; retain ciphertext and proof. If the owner's private key is
locked or unavailable, report the restore as blocked and keep it off the cluster.
A successful isolated check validates that recovery point, not PITR or a complete
host/identity reconstruction RTO.
