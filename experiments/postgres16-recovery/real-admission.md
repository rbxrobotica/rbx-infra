# Admission proposal: one live Commerce sandbox database

Prepared 2026-11-19 for operator decision. The source is the existing
`rbx_commerce_sandbox` database on Jaguar via its pinned SSH alias and local
Unix socket. This is a live sandbox database on a shared server. Owner sponsor
and cleanup owner proposed: Leandro Damasio. The Commerce owner must confirm
the dataset classification, approved bounds and current access. The [evidence
PR](https://github.com/rbxrobotica/rbx-resilience/pull/12) records the actual
catalog and custody observations. The synthetic runner explicitly targets its
own fixture and is unsuitable for this source.

## Preconditions for capture authorization

1. Identify the approved database owner, confirm the current data classification
   permits the entire sandbox dataset at the proposed destination and document
   its approved recovery objectives. The sandbox schema contains contact,
   payment and provider-related tables. The absence of other sessions and the
   historical sandbox designation do not classify current rows. Current
   off-site PostgreSQL backup/freshness evidence is unavailable; the verified
   historical restore concerns the pre-migration, source-host copy only.
2. Admit a dedicated private PostgreSQL archive bucket in Backblaze B2 US East
   under the existing organization account. Proposed scope
   `postgres16/commerce-sandbox/<run-id>/` with one version per run object;
   scope the writer to that prefix and write-only, and the independent verifier
   to read/list/retention metadata. No shared document bucket keys or account
   admin identity in either runtime. Require SSE-B2, version history and
   seven days of Compliance Object Lock. Propose 14 days of manual test
   retention from verified upload, with disposal subject to operator review
   and provider retention. Read back the actual bucket policy and retrieve an
   exact synthetic object version by the verifier; compare size and SHA-256
   against an independently recorded receipt. Account, bucket and object
   identifiers and credential values belong in private custody.
3. Record the approved public encryption recipient/subkey in private custody.
   Open the approved independently held recovery copy in a fresh isolated
   GnuPG home and agent on recovery hardware, with the operational keyring and
   password store absent. The owner must demonstrate access to the independent
   unlock factors without revealing them. Decrypt a harmless challenge
   encrypted to the exact subkey, match SHA-256 and integrity status, then stop
   the agent and discard the temporary keyring. Confirm B2 sign-in/recovery and
   the verifier are accessible without Jaguar or the producer identity. This
   gate is owner-assisted; successor custody is a separate qualification.
4. Qualify the cached amd64 PostgreSQL 16.15 glibc/Trixie candidate at
   `docker.io/library/postgres@sha256:485935f94cc7165afa896978809c37b592dc07f0a37d2c8f645f12412d0212c8`.
   Registry manifest and image config match. Before any real payload arrives,
   prove actual server/client versions and a synthetic `UTF8` database using
   the source libc locale `C.UTF-8` for collation and character classification.
   Compare representative ordering and application-supported behavior, check
   the `plpgsql` version and fresh target identity, and verify resource bounds.
   Require a reviewed executable single-source harness and offline safety
   checks first. If compatible provisioning or source identity fails, stop.

## Proposed authorization envelope

- **Source and scope:** one complete logical snapshot of
  `rbx_commerce_sandbox` (schemas `commerce` and `public`), with its database
  metadata, data, sequences, schema objects, ownership and applicable ACLs.
  Reconcile every referenced owner, grantee and role setting before capture.
  Package target-only role recreation for the sandbox owner/runtime role and
  target administration with restricted attributes, without passwords or
  cluster-wide globals. Exclude other databases, cluster role credentials,
  replication, WAL, provider/API secrets outside the database and system
  configuration. Values stored in the authorized database travel inside the
  encrypted artifact only. Abort on an unrelated dependency or unapproved
  data class.
- **Source access and limits:** one operator-approved 15-minute window, one
  attempt, 16 MiB maximum observed source database size, 32 MiB maximum
  ciphertext and 120 seconds maximum source snapshot/capture. Use reviewed
  PostgreSQL 16.15 `pg_dump -Fc --create --snapshot` with an exported read-only
  snapshot shared by the dump and expected-manifest checks. No directory-format
  workers. Use the allowlisted source Unix socket, verified hostname and
  system identifier, explicit database and role, cleared inherited defaults and
  short statement and lock timeouts. Recheck contemporaneous sessions and
  expected catalog/locale; stop on unapproved writers, drift, wait or bounds
  failure. The source database keeps its current configuration and workload.
- **Custody:** encrypt on Jaguar using only the approved public recipient,
  without persisting a cleartext dump. Close the ciphertext before computing
  its SHA-256, byte count and the trusted receipt hash; compare the provider
  readback, publish the receipt last and hold its trust anchor separately from
  B2. One ciphertext and one receipt, and no provider-side policy override.
  Record cutoff, upload, verification and freshness. Close on failure without
  a success receipt. Retry or rescheduling needs a new decision.
- **Restore:** on ThinkCentre, independently retrieve the exact version over
  B2 TLS using the verifier identity, check the anchored receipt and cipher
  hash, and decrypt in disposable private tmpfs. Use a new server system ID
  with rootless Podman, network none, no port publication, no production
  credentials, no source data directory and verified nonce ownership.
  Recreate only approved roles before `pg_restore --exit-on-error --create`.
  Budget at most one target, 1 CPU, 512 MiB memory, 64 PIDs and 256 MiB
  PGDATA/scratch tmpfs space per mount; 10 minutes of restore-phase wall time.
  Measure retrieval, unlock, verification, provisioning, restore and first
  application-semantic read separately.
- **Acceptance:** compare the trusted schema, role/ACL, constraints, indexes,
  sequence and per-table count/digest manifest against the restore. Hold
  data-derived expectations and digests inside encrypted custody. Publish
  sanitized match/failure outcomes in Git. Check `public.schema_migrations`
  against the capture and require a clean tracker. Execute a read-only
  Commerce check as the restored sandbox role covering the ledger cutover
  singleton and subscription/invoice relation counts, with expected values
  derived from the same source snapshot. Start no Commerce service, migration,
  Asaas callback, e-mail or worker. OCI/SSH/B2 retrieval takes place outside
  the network-isolated database runtime.
- **Disposition:** stop and remove only the verified target and temporary
  GnuPG state after durable sanitized evidence. Retain the approved ciphertext
  and receipt for the defined review window. Preserve source state and any
  prior archive. This window authorizes no deploy, provider policy/key mutation,
  source database write, periodic backup, or PITR. Active runbook and
  actual capacity must be checked again before approving execution.

Decision order: authorize only the scoped custody preparation and participate
in the clean key-recovery challenge first. Approve the capture envelope only
after those outcomes, data classification, storage/target checks and the
executable capture harness have been recorded. There is no active capture
authorization in this proposal.

## Dedicated B2 destination checkpoint, 2026-11-20

The dedicated private US East bucket was created with Object Lock. B2
readback verified SSE-B2 AES256, a seven-day default Compliance period and
no lifecycle deletion rules. Two 30-day application keys authorized with
exact bucket and prefix restrictions: the writer has `writeFiles`; the
verifier has `listFiles`, `readFiles` and `readFileRetentions`. The destination
locator, separate keys and canary version were preserved in the private
encrypted credential remote and compared after a fresh checkout. No admin
credential is used by the verifier.

One 46-byte synthetic object was uploaded under the pilot prefix. A second
process authorized only as the verifier, listed all versions under that
prefix and retrieved the single exact version. Its size, SHA-256, SSE-B2 and
Compliance expiry matched the trusted receipt. Anonymous retrieval and an
upload-grant request as the verifier both returned HTTP 401. The canary has
seven days of Compliance retention and 14 days of manual review retention.
The provider was not asked to delete the locked canary.

See the [evidence PR](https://github.com/rbxrobotica/rbx-resilience/pull/12)
for the receipts. This checkpoint closes dedicated destination preparation and
the synthetic storage check. Before real capture, repeat identity, scope,
policy and freshness checks. The owner-assisted recovery key challenge,
database classification and objectives, target/harness qualification and
explicit capture authorization are still required.
