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
The recurring configuration is active; observed execution evidence is recorded
below. An explicit Job is not proof of the first scheduled execution.

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
04:30 America/Sao_Paulo, after the cluster backup's maximum Job deadline,
with persistent catch-up. A failed copy retries after 30 seconds, with at most
three starts in one hour and a 360-second bound per attempt. Exhausted retries
remain a visible service failure; no privacy or integrity gate is skipped.
The installer re-arms the timer after updating its calendar. If the new time
has already passed, a persistent timer catch-up is a scheduler invocation and
must be recorded as catch-up, not an exact on-time dispatch.
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
The running server's actual `data_directory` and its resolved filesystem path
must both be `/var/lib/postgresql/data`, the admitted tmpfs. A `PGDATA` or `-D`
override pointing at the writable container layer is rejected before decryption.

Start a disposable PostgreSQL16 instance in a separate terminal with a build
lease in run mode. The attached podman helper adds workstation cache binds,
which this isolated restore target correctly rejects. The fixture password below is public and local-only:

```sh
rbx-build-cache run -- podman run --rm --name flightdeck-recovery-proof-TIMESTAMP \
  --network none --memory 768m --cpus 1 \
  --tmpfs /var/lib/postgresql/data:rw,size=512m \
  --env POSTGRES_USER=restore_owner --env POSTGRES_DB=flightdeck_restore \
  --env POSTGRES_PASSWORD=disposable_restore_only \
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

## Observed cluster activation and owner custody, 2026-11-16

PR 403 was merged at `4d00c430590be420c8f2b69949c46c9fcab48a6b`.
ArgoCD reconciled the nominal source S3 read permission before the five backup
resources. CronJob `rbx-flightdeck/rbx-flightdeck-backup` is active at 03:45
America/Sao_Paulo with the verified pinned image and no private recovery key.
The explicit initial Job completed at 04:34:40 UTC. Its exact remote version
was independently downloaded; ciphertext SHA-256 was
`8ad5dd564e15bdc5d65794f11c57c0f4135b4e04981a19d7041094a8015ecf24`.
The 154195-byte encrypted owner copy and cluster receipt are retained privately.

That cluster-produced archive was restored in isolation at 04:54 UTC; a second
fresh restore at 05:40 UTC took 0.645 seconds. Applying migrations 004–007 and
the canonical intake bootstrap to that restored database also passed, preserving
three Workstreams and 18 ledger entries with zero Actions or invalid constraints.
The disposable fixtures were stopped. Measured seconds cover the small local
restore, not the full four-hour recovery objective or PITR.

Owner tools are installed from infra revision
`cf1af75e0cd057d5075da83f62b4f525289e6495`; their source hashes are in
`~/.local/share/rbx-flightdeck-tools/backup/source.json`. The independent-copy
service succeeded with a receipt stamped 05:50 UTC, retaining an exact-version SHA-256-verified
ciphertext on the owner host. The initial timer was set to 04:15 local time. A transient
S3 listing failure on the first attempt was reported as unknown and retained
as a failed attempt; the later successful readback established current custody.

The restore and upgrade evidence bundle was encrypted, archived under the
private `flightdeck/backups/restore-proof/` prefix and independently downloaded
by exact version. Its SHA-256 is
`50628a6497e61283a0e41f5016184f90910bbbaab17563109bc86bcb0cbda1f3`.
Private receipts, downloaded ciphertext, restore proofs and source evidence live
in the owner directories identified above; no decrypted database dump is there.

At 06:01 UTC all three backup alert rules were loaded with health=ok. Prove a
real dispatch using CronJob lastScheduleTime, the Job's controller owner UID and
`batch.kubernetes.io/cronjob-scheduled-timestamp`, then receipt and exact-version
download/hash. Compare parsed timezone-aware instants: the scheduled annotation
can use a local offset while CronJob status uses UTC. An explicit Job's
lastSuccessfulTime does not establish that proof.

After real chat ingestion and review preparation, the explicit
`rbx-flightdeck-backup-post-intake-20261116` Job produced a new full archive.
Its exact version was independently downloaded; the 205395-byte ciphertext has
SHA-256 `02809ad9660b75c2265dfe28854564e8e81d96e5e5e365c32ead2f881e953956`.
The installed guarded checker restored it at 06:16 UTC into a fresh no-network,
tmpfs PostgreSQL16 target. It recovered all seven machine captures, one weekly
snapshot, migrations 001–007, 39 audit entries, three Workstreams and 18 ledger
entries. Actions, Signals, Outcomes, routed captures and invalid constraints
were zero. Restore/inventory took 0.903 seconds; the fixture was stopped.
The private proof is `post-intake-20261116.restore-proof.json`. This verifies
recovery of the newly captured data, not only an empty intake schema.

The owner workstation has recurring inode pressure from other local test
producers. An ENOSPC failure affected an evidence write during validation;
previous evidence and backups were preserved, and writing resumed when capacity
returned. The collector's last observed timer runs were healthy with no pending
records. Continue checking source/transport status and the existing storage
alerts; do not assume host-side collection or independent custody can continue
while the host cannot create files. The cluster backup and snapshot jobs do not
depend on that workstation. Do not stop other tasks or delete their volumes as
part of this runbook.

## First real daily dispatch and bounded retries

The scheduler created `rbx-flightdeck-backup-29913525` at
2026-11-16T06:45:00Z. The Job's controller owner UID matched the managed
CronJob, whose lastScheduleTime was 06:45 UTC. Its scheduled annotation was
`2026-11-16T03:45:00-03:00`, the same instant. No manual instantiation annotation
was present. This proves the real scheduler dispatch, not backup success.

That first scheduled Job failed at 06:46:55 UTC with BackoffLimitExceeded after
one pod exited with the safe error `Off-site archive verification failed`.
The initial configuration allowed no retry. Separate owner-host operations also
observed transient S3 metadata failures before later successful authenticated
privacy/versioning checks and exact-version readback. This supports bounded
retry as a mitigation; the generic producer log does not identify a specific
provider HTTP error for that failed Job. The failed Job and evidence are
preserved, along with the earlier verified backups.

The CronJob now admits two serial retries inside the existing 1860-second Job
deadline. Its 03:45 America/Sao_Paulo schedule, concurrency prohibition, image
digest, RAM and resource limits, encryption, private-bucket/versioning/readback
gates and retention are unchanged. No failed Job is relabeled or replaced.
Verify the reconciled backoffLimit and a fresh explicit retry-validation Job;
that validation must remain labeled explicit. The next real daily dispatch
after this correction is expected at 2026-11-17T06:45:00Z. A successful real
daily backup after the correction remains unproven until its own receipt,
remote version and independent download/hash are checked.

The explicit retry-validation Job completed at 07:13:40 UTC on 2026-11-16.
Its new 205395-byte ciphertext was independently downloaded by exact version,
with SHA-256
`725ad3ef4c232865fe433b6f7198e767fa7fae9e2d1ee160adcc875c5144fef6`.
It succeeded with one pod, so this proves the reconciled producer path and
configured retry bound, not an exercised pod retry or a successful daily
scheduler dispatch after the correction.

The first owner-copy timer invocation started at 07:15:21 UTC and exited with
status 1 at 07:15:52 UTC, with no restart configured. The generic log does not
prove a particular HTTP or filesystem cause. The newer exact-version download
above is retained independently on the owner host. The owner-copy service now
has bounded on-failure restarts and a 04:30 local schedule, after the cluster
Job's 31-minute maximum window. Check its real timer trigger, service result,
restart count and ciphertext receipt; do not count a manual start as a timer
dispatch. Preserve both failed-attempt evidence and earlier verified copies.
