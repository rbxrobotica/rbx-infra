# Public Presence recovery gate

This runbook covers the first private approved-handoff import and the database
migration that precedes the first Public Presence deployment. It does not
authorize campaign activation, remote publication or ad spend.

## Observed baseline (2026-09-28)

- `public_presence` exists on jaguar and has zero tables in the `public` schema.
  The `rbx-presence` bucket has no objects. These observations bound the current
  blast radius; they do not stand in for future backup protection.
- The daily `pg-backup-s3.sh` cron ran at `2026-09-28T011502Z`. Its 22 files,
  including `globals.sql.gz` and `public_presence.dump`, are present under
  `s3:rbx-backups/jaguar/postgres/2026-09-28T011502Z/`. Local retention is two
  days and remote retention is 14 days. The cron redirects output to
  `/var/log/pg-backup-s3.log`; no failure or age alert was found.
- A restore drill at `2026-09-28T062347Z` to `2026-09-28T062356Z` streamed
  the two remote files into an isolated PostgreSQL 16 container with no
  network and tmpfs storage. The bootstrap `postgres` role was skipped when
  applying `globals.sql.gz`, because it already exists in a fresh cluster.
  All other globals and `public_presence.dump` restored with fail-on-error.
  The recovered `public_presence` role exists and the database has zero public
  tables. The container was removed. This proves readability of that backup,
  not a full application recovery time.
- The versioned drill command in `bootstrap/scripts/test-public-presence-restore.sh`
  was run against that same legacy backup on 2026-09-28 and returned
  `role=true tables=0`. It ran Podman locally with no container network and
  streamed the files from jaguar over SSH. The next drill must use the new
  protected bucket and dedicated key.
- `rbx-backups` and `rbx-presence` currently have versioning unset. The
  backup bucket has no Object Lock configuration. The jaguar backup and the
  Public Presence workload use the same S3 access key. The backup bucket has
  another prefix, `robson-db-archive/`; changing bucket-wide controls must
  account for it.
- Contabo states that Object Storage is encrypted in transit and at rest.
  An absent S3 bucket-encryption configuration therefore does not by itself
  prove that provider-side encryption is absent. Client-side encryption and
  independent key recovery have not been demonstrated.

## Targets for owner acceptance

For private import only, propose **RPO at most 24 hours** and **RTO at most
4 hours** for `public_presence`. The current daily dump schedule can only
support the proposed RPO when every run and remote copy succeeds. The 9-second
database drill does not prove the proposed RTO because it excludes VPS,
Kubernetes, secrets, assets, DNS and application verification. Record owner
acceptance and a full recovery rehearsal before claiming these targets.
Reassess the objectives and add WAL archiving or equivalent before enabling
automatic publication or paid campaigns.

## Required protection before private import

1. In the Contabo Customer Panel, create a dedicated sub-user with the **S3
   Object Storage Read and Write** role and enforced 2FA. The invited operator
   must accept the invitation and retrieve that user's S3 keys from **Account
   → Security & Access → S3 Object Storage Credentials**. Save them locally in
   `pass` as `rbx/backup/postgres/access-key` and
   `rbx/backup/postgres/secret-key`, without sending them through chat or
   putting them in Git. Do not give the application this credential. Keep the
   encrypted password store in its private remote and verify an independent
   offline recovery copy of the GPG private key.
2. Review the read-only plan from
   `bootstrap/scripts/prepare-public-presence-backup-buckets.py`. Apply it to
   create `rbx-postgres-recovery-eu2` **with Object Lock enabled at creation**,
   default `COMPLIANCE` retention of 30 days, and versioning. This action
   also enables versioning on `rbx-presence`. Compliance retention cannot be
   shortened for existing protected objects. The
   existing `rbx-backups` bucket has other workloads and was created without
   Object Lock; leave it and its objects intact during migration. Provision
   through reviewed IaC, then run the new job and repeat the isolated restore
   from the new bucket.
3. Get the new user's S3 principal ARN from its Contabo tenant, customer and
   user IDs (see the linked Contabo policy guide). Run the read-only plan from
   `bootstrap/scripts/prepare-postgres-backup-user-policies.py`, inspect every
   named bucket and its existing policy, then apply the plan. It adds an
   explicit deny for the backup user to every bucket except the new backup
   bucket. The current inventory contains nine other buckets; rerun the plan
   whenever a bucket is added. `rbx-content`, `rbx-data-lake` and
   `rbx-presence` already have policy statements that must be preserved.
   The role alone does **not** confine the user to one bucket. Verify with the
   new key that listing objects in each other bucket returns AccessDenied.
   Keep the operator credential separate. The current application credential
   belongs to the existing storage account; before claiming an independent
   backup credential boundary, test whether it can access the new bucket. If
   it can, move the application to its own scoped sub-user and rotate its
   `pass`/Kubernetes secret through the controlled Ansible flow.
4. Apply `bootstrap/ansible/postgres-protected-backup.yml` after the dedicated
   key exists. It materializes a root-only rclone config from `pass`, schedules
   the new job, and copies both PostgreSQL dumps and the current assets in
   `rbx-presence` to the locked bucket. An immutable manifest records even an
   empty asset snapshot. The job does not delete remote backups. The old cron
   remains active until the new job has completed and been restored from once.
5. Sync the monitoring change and verify that
   `rbx_postgres_backup_last_success_timestamp_seconds` is scraped from jaguar
   and the `RBXPostgresProtectedBackupStale` alert is inactive. The alert fires
   when a protected backup is absent or older than 36 hours.
6. Record the accountable operator, executable recovery steps, and the tested
   result in this runbook. Run `bootstrap/scripts/check-public-presence-backup.py`
   with the dedicated backup rclone config and the SHA-256 of the workload
   access key. Every check must pass. Do not print or commit either key.

The backup checker verifies that the **backup** key cannot list other buckets.
The reciprocal application-key check and any necessary application-key rotation
remain a separate acceptance gate after the new bucket exists.

## Release sequence after the gate passes

1. Materialize the already-declared `ghcr-pull-secret` with the controlled
   `k8s-secrets` Ansible role. Compare live and desired objects before sync.
2. Promote one verified pair of API and web image tags from the same commit in
   `apps/prod/rbx-public-presence/kustomization.yml`. Keep a known-good
   rollback tag. Sync ArgoCD and verify the migration Job and both Deployments.
3. Configure the approved-handoff source commit and read-only GitHub token via
   `pass` and Ansible. Import only into private staging and verify all nine
   approved assets, hashes, decision records and source lineage.
4. Keep campaign activation and paid publication blocked until Growth,
   FlightDeck, Commerce, channel authority and provider receipt gates pass.

## Read-only checks

The backup checker exits nonzero for stale or incomplete backups, shared S3
credentials, a backup key that can list other buckets, missing asset snapshot,
missing versioning or missing compliance retention. It does not
change buckets or secrets. It is intentionally stricter than the current
baseline, so it fails until the protection work above is complete.

```bash
python3 bootstrap/scripts/prepare-public-presence-backup-buckets.py \
  --config /root/.config/rclone/rclone.conf \
  --remote s3 \
  --backup-bucket rbx-postgres-recovery-eu2 \
  --assets-bucket rbx-presence

# After reviewing that exact plan and receiving the operator's apply approval:
python3 bootstrap/scripts/prepare-public-presence-backup-buckets.py \
  --config /root/.config/rclone/rclone.conf \
  --remote s3 \
  --backup-bucket rbx-postgres-recovery-eu2 \
  --assets-bucket rbx-presence \
  --apply

python3 bootstrap/scripts/prepare-postgres-backup-user-policies.py \
  --config /root/.config/rclone/rclone.conf \
  --backup-bucket rbx-postgres-recovery-eu2 \
  --backup-principal-arn '<Contabo S3 user ARN>'

# After reviewing the affected bucket policies with the operator:
python3 bootstrap/scripts/prepare-postgres-backup-user-policies.py \
  --config /root/.config/rclone/rclone.conf \
  --backup-bucket rbx-postgres-recovery-eu2 \
  --backup-principal-arn '<Contabo S3 user ARN>' \
  --apply

python3 bootstrap/scripts/check-public-presence-backup.py \
  --config /root/.config/rclone/postgres-backup.conf \
  --remote postgres-backup \
  --operator-config /root/.config/rclone/rclone.conf \
  --bucket rbx-postgres-recovery-eu2 \
  --assets-bucket rbx-presence \
  --workload-access-key-sha256 <sha256-of-workload-access-key>

# From an operator workstation with local Podman and SSH access to jaguar.
bash bootstrap/scripts/test-public-presence-restore.sh \
  jaguar /root/.config/rclone/postgres-backup.conf \
  postgres-backup:rbx-postgres-recovery-eu2/jaguar/postgres \
  <latest-protected-backup-stamp>
```

## Sources

- [Contabo Object Storage encryption and redundancy](https://docs.contabo.com/docs/storage/object-storage/)
- [Contabo versioning](https://help.contabo.com/en/support/solutions/articles/103000282907-does-object-storage-support-versioning-)
- [Contabo Object Lock creation requirement](https://help.contabo.com/en/support/solutions/articles/103000282887-how-do-i-use-object-locking-on-files-in-my-object-storage-)
- [Contabo bucket access control](https://help.contabo.com/en/support/solutions/articles/103000282920-can-i-restrict-users-to-specific-buckets-in-contabo-s-object-storage-)
- [Contabo sub-user roles](https://help.contabo.com/en/support/solutions/articles/103000408305-how-can-i-add-more-users-under-my-account-)
- [Contabo S3 credential location](https://help.contabo.com/en/support/solutions/articles/103000282843-how-do-i-generate-access-and-secret-keys-for-object-storage-)
