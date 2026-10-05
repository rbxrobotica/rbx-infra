# Comms preservation and delivery monitoring

Status: **staged, not active**. The PostgreSQL backup CronJob is suspended and
its image tag is a placeholder. Publication, a reviewed digest promotion,
first live archive/restore proof and observation of a scheduled run are separate
gates. The existing Plausible backup and its cadence do not cover Comms.

## Scope and boundaries

The runner backs up exactly the external PostgreSQL database `rbx_comms` through
the existing `rbx-comms-secrets/DATABASE_URL` reference. It refuses any other
database name and forces `PGOPTIONS=-c default_transaction_read_only=on` for
`pg_dump`. This is a whole-database logical dump, including the Comms schema,
submission records, delivery outbox/status history and any other schemas already
inside this same database. It is not a host-wide backup or a backup of another
database. There is no PostgreSQL server added to the production cluster.

The custom-format PostgreSQL 16 dump and its manifest are bundled, encrypted to
the pinned owner public key, then stored under `comms/backups/full/` in the
private, versioned `rbx-data-lake` bucket. Existing `contabo-s3-credentials`
provides the same two S3 credential keys already projected for Comms. Each run
uses a unique UTC/UUID object name, verifies the exact uploaded version by
reading it back and comparing its SHA-256, and emits a receipt. No objects are
deleted or expired by this implementation. There is no Object Lock guarantee.

The Comms-only S3 CLI shares the tested transport with Plausible but explicitly
selects the `comms` scope. The existing Plausible CLI, default namespace and
receipt schema remain unchanged. Neither environment variables nor a supplied
object key can move the Comms client into the Plausible collection.

Credentials are passed only through the child environment needed for each
operation, never command arguments, manifests or logs. The Job receives only
the public GPG key, not its private counterpart. It rejects a wrong, revoked,
expired or private key before querying PostgreSQL. The dedicated ServiceAccount
has no Kubernetes API role or mounted token, and the image does not contain
kubectl. The deployment is UID 10001, read-only root filesystem, all Linux
capabilities dropped, memory capped at 512 MiB and CPU capped at one core.

The full run has an 1800-second deadline and a 1 GiB maximum per artifact;
Kubernetes adds a 1860-second Job deadline, no automatic retry and a 4 GiB
private staging volume on disk. Intermediate plaintext is mode 0600 within a
0700 directory and removed on success or failure. Encrypted node storage has
not been established; unlinking is not secure erasure. The cluster's temporary
plaintext is not protected by the remote archive encryption.

The database and S3 share Contabo as a provider. Independent-provider recovery,
Object Lock, database role recovery, infrastructure secrets, private-key
recovery and attachment object preservation are separate responsibilities.
This database archive includes attachment references, **not attachment blobs**
in `rbx-comms-attachments`. Nothing here asserts unconditional recoverability.

## Publication and activation

The `comms-backup-image.yml` workflow runs unit/failure tests, the existing
Plausible regression tests, Bandit, secret and dependency scans, a container
build, a real synthetic PostgreSQL/GPG restore and a final image vulnerability
scan. Only a successful push to main publishes the exact scanned image to
`ghcr.io/rbxrobotica/comms-backup:sha-<commit>`. PR builds do not publish.
The synthetic restore has a stubbed S3 transport; it is not production archive
or live storage evidence.

Before unsuspending the 03:30 `America/Sao_Paulo` schedule:

1. Verify the published registry digest and actual pull access. Pin that digest
   in a reviewed GitOps promotion; do not replace the placeholder speculatively.
2. Verify the existing DB/S3 ExternalSecrets are ready using metadata only, the
   projected GPG key matches its pinned fingerprint, and the namespace's pull
   access is sufficient. Do not log secret data or full provider errors.
3. Create one controlled Job from the reviewed CronJob and retain its safe
   receipt. Download its exact S3 version independently, verify ciphertext hash,
   decrypt on the owner host and validate the manifest/dump hashes.
4. Restore into an isolated PostgreSQL 16 sandbox with no production mounts,
   credentials, published ports or external network. Verify expected schemas,
   submission/outbox counts and logical fingerprints without printing message
   content or personal data. Record and encrypt a separate restore proof with
   `--artifact-kind restore-proof`. Only then add `restore_tested_at` to an
   enriched receipt; preserve the original runner receipt unchanged.
5. Observe the first controller-scheduled Job and its receipt. A manual Job can
   update `lastSuccessfulTime`; that field alone does not prove the schedule ran.
   Confirm CronJob ownership and scheduled timestamp as well.

Use the Comms wrapper when inspecting its storage. `latest` verifies presence
and metadata, not a new content download or a successful restore:

```bash
python3 scripts/comms/s3_archive.py latest --artifact-kind full --lookback-days 3
```

The intended cadence is daily, with a 24-hour recovery-point target and a
26-hour freshness alert. Job success establishes neither indefinite retention
nor current decryptability. Review the independent download and restore
receipts; rehearse restoration monthly and after material schema/tool changes.

## Founder notification and internal monitoring

The runtime recipient configuration is
`FOUNDER_ALERT_RECIPIENTS=ceo@rbxsystems.ch,contact@rbxsystems.ch`. Preserving a
submission and queueing a notification do not prove provider acceptance,
delivery or human reading. The API's durable `contact-v1` outbox and provider
callbacks carry those separate states. Preserve the existing Postmark/Meta
webhook security configuration and verify its credential prerequisites before
promoting a new API image.

The ServiceMonitor scrapes the existing internal Service's `/metrics` endpoint
on its `http` port every 30 seconds. The public HTTPS ingress remains an explicit
allowlist with no route for `/metrics`. No ingress or webhook route is changed
by the monitoring addition.

The contact metrics have no recipient, message-body or submission-ID labels:

- `rbx_comms_contact_outbox_messages{status=...}`: contact-v1 queue state counts.
- `rbx_comms_contact_outbox_oldest_pending_seconds`: age of the oldest pending notification.
- `rbx_comms_contact_outbox_observation_success`: zero when the database cannot be observed.
- `rbx_comms_contact_outbox_observed_at_seconds`: timestamp of the last successful observation.

Both replicas observe the same database. Alerts use max/min rather than summing
those gauges: a pending age over 15 minutes for five minutes, a missing/failed
observation or scrape for five minutes, an observation over five minutes old
for five minutes, and any failed/uncertain/bounced contact
notification for five minutes. An uncertain send must be reconciled against
provider evidence before retrying; otherwise a duplicate email is possible.
An inactive alert does not prove email delivery, and registering rules does not
prove that Alertmanager delivered a notification to an operator.

The existing Alertmanager warning/critical route addresses
`ceo@rbxsystems.ch` through the Mailcow/Postmark email path. No new receiver is
introduced here. That alert delivery still depends on email infrastructure;
it is not an independent notification channel for an email-provider outage.

The separate backup rules report missing CronJob, failed runs and no successful
run in 26 hours. After activation verify rule evaluation, scrape target health
and the operator notification route. Before activation the suspended placeholder
is not backup coverage; absence of a verified archive remains a known gap.
