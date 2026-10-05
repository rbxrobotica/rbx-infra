# Plausible preservation and weekly review

Status recorded on **2026-10-05**: the initial encrypted backup of both databases
was uploaded, downloaded and hash-verified; an isolated restore succeeded.
The daily Job and its monitoring are **not operational until their GitOps
deployment and first verified receipt have been observed**. Building an image,
merging manifests or scheduling a chat alone does not establish recurring backup.

This runbook complements [Plausible bring-up](PLAUSIBLE-BRINGUP.md). Runtime
manifests belong in `apps/prod/plausible/`; the build pipeline is
[`plausible-backup-image.yml`](../../.github/workflows/plausible-backup-image.yml).
Production changes and rollback follow GitOps. Do not apply manifests manually.

## What is protected

| Data | Source | Archive representation |
| --- | --- | --- |
| Sites, users, goals, settings and migrations | PostgreSQL 16, external to Kubernetes at `161.97.147.76:5432` | `postgres.dump`, custom-format `pg_dump` |
| Events, sessions, imported data and database objects | `plausible_events` in `plausible/plausible-clickhouse-0`, on `jaguar` | `clickhouse.tar`, native database backup |
| Source versions, snapshot times and archive hashes | Backup runner | `manifest.json` inside the encrypted bundle |
| Weekly institutional-navigation evidence | Aggregate-only ClickHouse collector | Versioned JSON input, report JSON and Markdown |

**Both database failure domains include `jaguar`.** External PostgreSQL means
outside Kubernetes; `161.97.147.76` is the same host that runs the ClickHouse pod.
A host or disk failure can affect sites/settings and events together. The
ClickHouse `local-path` volume is not replicated and cannot be expanded in place.

PostgreSQL and ClickHouse are backed up with independently consistent snapshots.
They do not share a transaction or one exact snapshot instant; both time ranges
are recorded. Restoring the pair requires checking that site and goal metadata
still match the restored event store.

The archive does not replace recovery of application secrets, the PostgreSQL
role, GitOps configuration or the owner's encryption key. Preserve those through
their existing secret-management and infrastructure recovery procedures.

## Protection and limits

The runner encrypts the bundle with OpenPGP before upload. The backup workload
receives only the public key and its exact recipient fingerprint. The private
key remains with the owner and is never mounted in the cluster. The runner
rejects private, expired, revoked or mismatched key material. Losing the private
key makes the encrypted archive unrecoverable; recoverability includes the key.

Ciphertext is stored in the private, versioned `rbx-data-lake` bucket at
`https://eu2.contabostorage.com`. Upload checks the bucket's access policy and
versioning, writes a unique key, obtains the exact version, downloads that
version and checks its size and SHA-256. A receipt records this evidence.

This is protection against a node/disk loss with tested recovery, **not an
unconditional guarantee**:

- The VPS and object storage use the same provider, Contabo. An independent
  provider/failure domain has not been established.
- Object Lock/WORM is not configured. Versioning is not protection against an
  administrator deleting all versions, account compromise or provider loss.
- A verified upload proves ciphertext integrity. Only a successful decryption
  and isolated database restore demonstrate recovery for that backup.
- The initial encrypted archive also has an owner-host copy. The scheduled
  runner's temporary local files are removed; it does not promise a permanent
  second local copy for every future run.
- No automatic object deletion or expiration policy is introduced. Storage
  usage and a future retention decision require review before changing this.

The live **14-month ClickHouse TTL** applies to raw events and sessions in the
working database. It is not the backup-retention policy. Old encrypted backups
may contain records that the live TTL has since removed. Weekly aggregate
archives are also a separate retention decision. Do not silently extend a raw
data policy by assuming that a backup and an aggregate are equivalent.

## Initial verified recovery evidence

The initial archive receipt is anchored by:

| Field | Value |
| --- | --- |
| Bucket | `rbx-data-lake` |
| Historical object key | `plausible/backups/2026-10-05/20261005T191524Z_393253eff00d4cd2aca003f97b795ab3.gpg` |
| Object version | `cS6o0dquE-K651XalSifQpmMfAW9-uA` |
| Ciphertext bytes | `2306795` |
| Ciphertext SHA-256 | `90d6e8a87415eda22c353c8e5b66b419ab997684efb14892d9dc052095b0b0c6` |
| Manifest SHA-256 | `91ef29efaeeef895489979c2d09d83fabba30a0fd669668fe5766caf233585a8` |
| Object verified at | `2026-10-05T19:15:33.008659Z` |
| Restore tested at | `2026-10-05T19:28:26.199335Z` |

The owner-host encrypted copy is
`/home/psyctl/.local/share/rbx-backups/plausible/20261005T191523Z_7b8e0183d605483f9f241e672cc88faf.tar.gpg`.
The local restore proof is
`/home/psyctl/.local/share/rbx-backups/plausible/restore-proof-20261005.json`.
Temporary files under `/tmp` are staging, not a durable archive location.

Recovery ran in isolated Podman containers with `--network none` and did not
write to production. PostgreSQL **16.15** restored 4 sites, 48 goals and 222
migration records. ClickHouse **24.12.6.70** restored 23 database objects,
542 events and 204 sessions. Event/session logical fingerprints matched the
live source. These counts describe this specific snapshot, not future data or
an ongoing recovery SLA. No recovery time objective has yet been established.

New archive keys use these prefixes:

```text
plausible/backups/full/YYYY-MM-DD/...
plausible/backups/weekly/YYYY-MM-DD/...
plausible/backups/restore-proof/YYYY-MM-DD/...
```

The first object predates these prefixes. **`latest --artifact-kind full` does
not search the historical key above.** Address that receipt's key and version
directly, or use a separately verified full-prefix copy and its new receipt.
An absent result from `latest` must not erase or invalidate the historical proof.

The same ciphertext was also verified under the new full prefix on 2026-10-05:

| Artifact | Object key | Version |
| --- | --- | --- |
| Full backup, same ciphertext SHA-256 as above | `plausible/backups/full/2026-10-05/20261005T192941Z_f54b49fdc73046dab145b0bf52d55614.gpg` | `o3Vz3RQ7Zuy6u4.X9U.7vMnToYwdOjU` |
| Encrypted restore proof | `plausible/backups/restore-proof/2026-10-05/20261005T192951Z_8816b894327e4778b52f60e19eb45807.gpg` | `IwBW2Cq.7QXp2MvaCwD4KE-y0b1AbtQ` |

The full-prefix copy was verified at `2026-10-05T19:29:51.656645Z`.
The restore proof was verified at `2026-10-05T19:29:55.628151Z`; its ciphertext
SHA-256 is `14e3f760bdfb17913c78f3fb2a73b8045f217e0cd642c3ee30a3f05c79ae3013`.
Their durable local receipts are `initial-full.receipt.json` and
`restore-proof-20261005.receipt.json` in the owner-host backup directory above.
The new full-prefix copy is discoverable by the bounded `latest` search while
its date remains inside that search's lookback window.

## Daily operation and evidence

The intended daily cadence has a **24-hour recovery-point target** and a
**26-hour freshness threshold**. Those are operational targets, not current
guarantees. Mark the cadence active only after verifying all of the following:

1. The immutable GHCR image exists and the reviewed GitOps manifests have
   reconciled to the expected CronJob, RBAC, configuration and Secret references.
2. The first Job completes and produces a valid full-backup receipt containing
   the expected object key/version, ciphertext hash and manifest hash.
3. A versioned download verifies that receipt, independently of Job completion.
4. The next scheduled run is observed and the freshness check reports failures
   or a backup older than 26 hours. Absence of evidence is unhealthy.

The implementation is
[`backup_runner.py`](../../scripts/plausible/backup_runner.py). Its configuration
comes from mounted secret references and environment variables. Required values
are `DATABASE_URL`, `BACKUP_PUBLIC_KEY_FILE`,
`BACKUP_RECIPIENT_FINGERPRINT`, `AWS_ACCESS_KEY_ID` and
`AWS_SECRET_ACCESS_KEY`. Optional storage settings include
`S3_ARCHIVE_ENDPOINT`, `S3_ARCHIVE_BUCKET` and `AWS_REGION`.
Do not print them, put credentials in command arguments, or paste them into Git.

Run the runner only in the configured, authorized backup environment:

```bash
umask 077
python3 scripts/plausible/backup_runner.py > backup-receipt.json
```

Plaintext staging uses private permissions and bounded temporary storage; encryption
of the current node filesystem has not been established. These temporary files
and the native ClickHouse staging file are not protected by the archive encryption.
Use encrypted storage for staging where available. The default per-artifact
limit is 1 GiB; limits and timeouts are bounded by the runner. A limit failure is visible and is not permission to remove the bound.
Normal completion and handled errors unlink temporary files. SIGKILL, OOM or
node failure can interrupt cleanup; inspect residual staging after failure.
Unlinking is not secure erasure on an unencrypted filesystem. PostgreSQL dumps
set `default_transaction_read_only=on` defensively; this does not reduce the
underlying application credential permissions.

ClickHouse native staging has a deliberately important path detail:

```text
BACKUP ... TO File('backups/plausible_<run-id>.tar')
actual path: /var/lib/clickhouse/backups/backups/plausible_<run-id>.tar
```

The second `backups/` is part of the current native path. The runner transfers
that exact file in 512 KiB blocks. The real exec transport truncated full-stream
output, sometimes with exit status zero; a successful process is not evidence
of a complete archive. Every block must have its expected length, and native
size/SHA-256 before and after transfer must equal the completed local copy.
The image uses the tested SPDY transport with TLS and the same scoped RBAC.

The transfer has one shared deadline (1,800 seconds by default), a fixed byte
cap (1 GiB by default), and no per-block retries. Total ClickHouse exec
invocations are `ceil(native_bytes / 524288) + 7`, recorded in the receipt:
14 for the verified 3,345,408-byte source. Larger archives can exceed the usual small
operation call budget; this is a deliberate, size-derived transfer bound, not
unlimited polling. API HTTP calls can exceed exec invocations. Review duration
and source growth before increasing either limit. The Job has a separate
one-hour deadline.

The runner removes only its own native staging file after verified
off-site persistence; failed uploads leave it in place for investigation.
Monitor this directory and PVC free space after failures. Do not delete a
backup merely because its Job failed or delete other runs' files indiscriminately.

The storage CLI is [`s3_archive.py`](../../scripts/plausible/s3_archive.py).
With credentials already available in the approved process environment:

```bash
python3 scripts/plausible/s3_archive.py latest --artifact-kind full --lookback-days 3
python3 scripts/plausible/s3_archive.py head "$PLAUSIBLE_BACKUP_KEY" \
  --version-id "$PLAUSIBLE_BACKUP_VERSION"
python3 scripts/plausible/s3_archive.py download "$PLAUSIBLE_BACKUP_KEY" \
  --version-id "$PLAUSIBLE_BACKUP_VERSION" \
  --expected-sha256 "$PLAUSIBLE_BACKUP_SHA256" \
  --output /secure/restore/backup.tar.gpg
```

The three variables above are non-secret fields copied from the selected
receipt. `latest` uses a bounded date-prefix search and versioned HEAD metadata;
it is not a restore test or a new full-content hash verification. For the initial
historical object, use its exact receipt instead of the new-prefix search.
The destination of `download` must not already exist.

## Sunday Flight Deck review

A Codex chat follow-up was configured on 2026-10-05 for **Sunday at 18:00,
America/Sao_Paulo**. It requires the owner computer and desktop app to remain
available, along with the existing secure access. It preserves encrypted weekly
reports and a local encrypted copy of the most recent verified backup. The
collector itself does not install a scheduler. A Sunday review occurs before the calendar week
ends; it must not be labeled a completed Monday-to-Sunday week.

[`collect_weekly.py`](../../scripts/plausible/collect_weekly.py) executes one
aggregate SELECT with a 20-second query limit, 256 MiB memory limit and 32 MiB
result limit. A complete JSON envelope, expected columns and matching row count
are required; a truncated stream becomes unknown even if exec exits zero.
It accepts no raw visitor identifiers, sessions, IP addresses,
page paths, query strings or free-form event properties. Offer version
`2026-10-05` excludes the QA version. Eight permitted events and fixed dimensions
are used; timestamps, offer and version are typed ClickHouse parameters.

```bash
python3 scripts/plausible/collect_weekly.py \
  --kubeconfig /secure/operations/kubeconfig \
  --instrumented-since rbxsystems.ch=2026-10-05T18:34:48Z \
  --instrumented-since rbx.ia.br=2026-10-05T19:07:31Z \
  --backup-receipts /secure/weekly/backup-receipts.json \
  --output-dir /secure/weekly/reports
```

`--backup-receipts` is optional and takes a JSON list with `id`, `location`,
`manifest_sha256`, `verified_at` and optional `restore_tested_at`. Use a receipt
whose archive and restore evidence were actually verified; the renderer does
not perform those checks. The example does not expose credential values.

As of this evidence date, Swiss partnership instrumentation is known from
`2026-10-05T18:34:48Z`. The Brazilian release was subsequently verified with
two ready replicas of `sha-bf424b7` and HTTP 200 containing the new content;
its conservative coverage boundary is `2026-10-05T19:07:31Z`. Earlier snapshots
that recorded Brazil as unknown remain unchanged. A site without a verified
deployment timestamp must still be omitted and remain unknown. Successful
querying is not evidence that an uninstrumented site had zero interest.

The JSON and Markdown report contain:

- Current Monday-to-cutoff counts, the same local cutoff in the prior week,
  and the last complete Monday-to-Monday period.
- Explicit coverage, instrumentation time, extraction time and source watermark.
- A separate post-deployment subset when a full period was not instrumented;
  those counts do not acquire a whole-week comparison.
- Per-site event counts, portfolio evidence by product and bounded form-error
  categories. Division by zero or absent coverage produces `null`/`unknown`.
- Report content hash and supplied backup/restore receipt references.

`form_success` means service acceptance, not a qualified lead or acquired
customer. Event counts cannot reconstruct individual journeys, deduplicate
people across weeks or prove revenue. Missing events can reflect blocking,
opt-out or collection failure. A source failure writes an `unknown` report and
returns exit status 2; it never fills missing evidence with zero.

Raw collection and rendering are separate. To render an existing aggregate
snapshot again, use [`weekly_report.py`](../../scripts/plausible/weekly_report.py):

```bash
python3 scripts/plausible/weekly_report.py --input aggregate.input.json \
  --json-output weekly.json --markdown-output weekly.md
```

Flight Deck has no selected B2B Workstream or authenticated ingest integration
for these reports yet. The report is evidence for the human weekly review; it
does not automatically create a Signal, approve an action, consume capacity or
write CRM/financial facts. Preserve the Markdown and JSON together. If archiving
them in object storage, encrypt the bundle before
`s3_archive.py upload /secure/weekly/report.tar.gpg --artifact-kind weekly`.

## Restore procedure and recurring test

Perform a controlled restore test **monthly**, and after a database or Plausible
upgrade, key change or archive-format change. The Sunday chat follow-up is
configured to attempt this isolated procedure when the latest proof is at least
28 days old, and report a failure or proof older than 35 days. This is an
owner-host agent workflow, not an unattended cluster restore Job; the private
key must remain outside the cluster. Actual success still requires a new proof.

1. Select a specific full-backup receipt. Download its exact object version and
   verify the ciphertext SHA-256 with the archive CLI.
2. On an owner-controlled recovery machine, use the owner's private key to
   decrypt into protected storage. Never export the key into a cluster Secret.
3. Inspect the bundle before extraction. Verify `manifest.json` against the
   receipt's manifest hash and each database archive against the manifest.
   Do not extract unexpected paths, links or unbounded archive contents.
4. Start disposable PostgreSQL and ClickHouse containers at compatible pinned
   versions, with `--network none`, isolated writable storage and no production
   database credentials or mounts. Restore PostgreSQL with `pg_restore` and
   ClickHouse with its native `RESTORE DATABASE` facility.
5. Verify database object inventory, migrations, site/goal counts, event/session
   counts and logical fingerprints against the snapshot evidence. Recheck live
   TTL configuration when preparing a real service recovery.
6. Record timestamps, exact archive/object version, image versions, checks,
   results and any limitations. A failed test remains a failed proof; do not
   edit an upload receipt to imply that it passed.
7. Encrypt and archive the proof with `--artifact-kind restore-proof`, preserving
   its relationship to the tested full backup. Remove disposable plaintext and
   containers after the evidence is secured.

A disaster recovery into production is a separate authorized GitOps operation.
First rebuild the required infrastructure and recover the appropriate secrets,
then restore the databases while Plausible writes are controlled. Reconcile
schema versions and TTL, authenticate to the application, check sites/goals and
confirm fresh event ingestion in the restored database. Do not promote the
isolated test containers into production.

## Failure interpretation

| Evidence | Interpretation and next step |
| --- | --- |
| Job complete, no verified receipt | Backup not proven; inspect the failed evidence path |
| Object version/hash verified, no restore proof | Ciphertext preserved; recovery untested for that object |
| Daily backup older than 26 hours | Freshness target missed; investigate scheduling, resources, credentials and storage |
| S3 unavailable or permissions/versioning invalid | Runner fails; retain source staging, protect space and repair the archive path |
| Private key unavailable | Owner recovery is blocked even if the ciphertext is intact |
| Weekly source/coverage unknown | Fix observation; do not diagnose business performance from fabricated zeros |
| Isolated restore fails | Preserve failure evidence and repair recovery before asserting protection |

Local evidence may be inspected without a production mutation. Changes to
credentials, infrastructure, scheduling, retention or production recovery must
follow their authorized operational path and must not be inferred from a green
build or an aggregate report.
