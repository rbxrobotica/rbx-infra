# Comms preservation and delivery monitoring

Status: **active in production; first cluster archive and isolated restore
verified**. The CronJob is configured for 03:30
`America/Sao_Paulo`, independent of a desktop. Observation of the first real
scheduled run remains pending; the controlled initial Job does not prove that
schedule. The existing Plausible backup and its cadence do not cover Comms.

## Observed preservation proof — 2026-10-05

The GitOps-managed CronJob produced controlled Job
`rbx-comms-backup-initial-retry-20261005`, complete at 22:52:51 UTC after its
placement fix in PR #347. The encrypted archive has 174,675 bytes and SHA-256
`ee7f67c992dd37ec845165b8bfb9520c846e72f78cb18a8483644571dcf05a4d`.
Its versioned S3 receipt is preserved privately on the owner host at
`~/.local/share/rbx-backups/comms/cluster-initial-20261005.receipt.json`.

An independent exact-version download, ciphertext hash, owner-host decryption,
manifest/dump hashes and isolated PostgreSQL 16.15 restore all passed at
22:53:58 UTC. The sandbox restored 46 tables and recorded counts and aggregate
fingerprints without exposing personal data. It had no external network, ports,
production credentials or workers. Its container and temporary plaintext were
removed; the original ciphertext, unchanged receipt and separate encrypted
restore proof are retained in that private evidence directory. The encrypted
restore proof was also stored and read back under `comms/backups/restore-proof/`;
`cluster-initial-20261005.evidence-index.json` links the original and proof
receipts. There was no
logical row comparison against the mutable live source.

First expected automatic run after activation: 2026-10-06 at 03:30 São Paulo
(06:30 UTC). Confirm CronJob ownership and scheduled timestamp, not only
`lastSuccessfulTime`, then verify its receipt and exact remote version.

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

The published `sha-a051b67` image from successful workflow run `37381784249`
was pulled with an explicitly empty registry auth file. Its immutable digest is
`sha256:fc4a6037f035baa0d229bd86f744ceca1ddb720e672004d0b7ffad1209365124`.
The fetched image passed a network-isolated, read-only, capabilities-dropped
smoke check as UID 10001 with PostgreSQL 16.15, Python 3.11.2 and GnuPG 2.2.40;
it contains no kubectl. This establishes image availability and toolchain, not
a successful production backup. The suspended GitOps staging may be reconciled
to enable one controlled first Job before a separate schedule activation.
The Job container is named `backup`; its safe JSON receipt is emitted to stdout.
The ciphertext is uploaded to the exact versioned S3 key in that receipt, while
local staging is removed when the runner exits. Preserve the receipt from Job
logs before cleanup and independently download that exact object version.

The first controlled Job on 2026-10-05 failed before producing an archive:
PostgreSQL rejected the connection from a backup pod on `jaguar` under the
existing HBA policy. Bounded read-only probes on `tiger` and `altaica` confirmed PostgreSQL
16.15, `default_transaction_read_only=on`, and a complete custom-format dump.
Their temporary dumps were removed and no S3 archive was created by those probes.
Diagnostic Jobs have no CronJob owner and must not count as backup success.
The backup now selects verified application nodes rather than the analytics
node; this does not broaden PostgreSQL authentication or network permissions.
The real cluster archive and isolated restore subsequently passed the gate below.

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
   Keep outbound workers disabled during recovery. Before enabling them against
   a restored database, reconcile in-flight and uncertain messages with provider
   evidence: restoring an older outbox snapshot can otherwise resend accepted
   notifications.
After unsuspending, observe the first controller-scheduled Job and its receipt.
A manual Job can update `lastSuccessfulTime`; that field alone does not prove
the schedule ran. Confirm CronJob ownership and scheduled timestamp as well.

Use the Comms wrapper when inspecting its storage. `latest` verifies presence
and metadata, not a new content download or a successful restore:

```bash
python3 scripts/comms/s3_archive.py latest --artifact-kind full --lookback-days 3
```

The intended cadence is daily, with a 24-hour recovery-point target and a
26-hour freshness alert. The CronJob was observed with `suspend=false` and
`lastScheduleTime=null` after activation; its only success so far is the
controlled initial Job. Job success establishes neither indefinite retention
nor current decryptability. Review the independent download and restore
receipts; rehearse restoration monthly and after material schema/tool changes.

## Founder notification and internal monitoring

The reviewed API promotion sets the runtime recipient configuration to
`FOUNDER_ALERT_RECIPIENTS=ceo@rbxsystems.ch,contact@rbxsystems.ch`. Preserving a
submission and queueing a notification do not prove provider acceptance,
delivery or human reading. The API's durable `contact-v1` outbox and provider
callbacks carry those separate states. Preserve the existing Postmark/Meta
webhook security configuration and verify its credential prerequisites before
promoting a new API image.

API main workflow `37383808267` tested, scanned and published `sha-588d8fd`.
Pull using the existing namespace registry credential confirmed digest
`sha256:ae3369b77d3f5779b864e6e82c3ee29dd9f1c58730bdc515d50d7d165c119f98`
and the full source revision. The two dedicated callback ExternalSecrets were
Ready before promotion. Configure the provider only after both new replicas are
ready. On 2026-10-05 the promotion in PR #348 reconciled at revision
`53b5b145ae6f7ea33445ce14cd4ef4a56a536788`: ArgoCD reports Synced/Healthy,
both API replicas run the expected digest and report a fresh successful queue
observation. The canonical `comms.schema_migrations` is version 14, clean; the
separate public schema migration table is not the Comms migration gate.

All seven backup/contact rules evaluate with `health=ok`; both contact scrape
targets are up. All three Postmark routes reject unauthenticated requests inside
both API pods, and public metrics/inbox routes return 404 on both API domains.
The new contact-v1 queue baseline is empty. These observations do not exercise a
real lead or prove a real email reached either recipient. No synthetic customer
lead or email was sent as part of the rollout.

The reviewed API promotion adds a ServiceMonitor for the existing internal Service's `/metrics` endpoint
on its `http` port every 30 seconds. The public HTTPS ingress remains an explicit
allowlist with no route for `/metrics`. No ingress or webhook route is changed
by the monitoring addition.

The contact metrics have no recipient, message-body or submission-ID labels:

- `rbx_comms_contact_outbox_messages{status=...}`: contact-v1 queue state counts.
- `rbx_comms_contact_outbox_oldest_pending_seconds`: age of the oldest pending notification.
- `rbx_comms_contact_outbox_oldest_unconfirmed_seconds`: age of the oldest accepted
  notification without a delivery or bounce receipt.
- `rbx_comms_contact_outbox_observation_success`: zero when the database cannot be observed.
- `rbx_comms_contact_outbox_observed_at_seconds`: timestamp of the last successful observation.

Both replicas observe the same database. Alerts use max/min rather than summing
those gauges: a pending age over 15 minutes for five minutes, a missing/failed
observation or scrape for five minutes, an observation over five minutes old
for five minutes, and any failed/uncertain/bounced contact
notification for five minutes. `RBXCommsContactReceiptOverdue` warns when an
accepted notification lacks a final provider receipt for over one hour, sustained
for five minutes. Missing confirmation does not establish delivery failure or
authorize replay. An uncertain send must be reconciled against
provider evidence before retrying; otherwise a duplicate email is possible.
An inactive alert does not prove email delivery, and registering rules does not
prove that Alertmanager delivered a notification to an operator.

The existing Alertmanager warning/critical route addresses
`ceo@rbxsystems.ch` through the Mailcow/Postmark email path. No new receiver is
introduced here. That alert delivery still depends on email infrastructure;
it is not an independent notification channel for an email-provider outage.

The separate backup rules report missing CronJob, failed runs and no successful
run in 26 hours. After activation verify rule evaluation, scrape target health
and the operator notification route. Before activation the suspended schedule
is not backup coverage; absence of a verified archive remains a known gap.
