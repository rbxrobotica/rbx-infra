# ADR-0012: pgBackRest cluster backup for jaguar with off-site repo in Zurich

## Status

**Accepted** · 2026-08-31

## Context

jaguar (161.97.147.76) runs the shared PostgreSQL 16 cluster holding
every non-regenerable database RBX operates: ZITADEL (identity),
PowerDNS backend, Robson, Langfuse, data warehouse and commerce
sandbox. The earlier wording that the 2026-08-27 resilience audit proved
"no backup of any kind" and "no scheduled dumps" was too broad. Historical
[logical-backup records](../runbooks/JAGUAR-LOGICAL-BACKUP-HISTORY.md) describe
per-database dumps and globals to Contabo S3 in July, including one isolated
[Thalamus restore on 2026-07-18](../infra/evidence/2026-07-18-jaguar-thalamus-restore.md).
That does not prove continuity through August/October, restoration of every
database, or WAL/PITR. The audit's incomplete inventory is not an observation of
all backup mechanisms.

The later [2026-10-12 preflight](../../infra/irc/docs/POSTGRES-LIVE-PREFLIGHT-2026-10-12.md)
found no pgBackRest executable/repository and WAL archiving off. Its crontab
inspection was inconclusive and cannot establish absence of logical backups.
Current all-database coverage and observed RPO remain unverified here. The k3s
etcd snapshot pipeline is a separate state scope; its Jaguar copies do not
restore application databases.

Every byte of storage RBX controls sits in one Contabo account, so no
copy inside the current infrastructure can satisfy the audit's
off-site invariant (different provider AND different account).
ADR-0004 already selected rsync.net Zurich as the off-site target for
mail and rejected Hetzner (correlated German hosting), B2-as-primary
(Object Lock ceremony, CLOUD Act coherence cost, egress fees) and
Storj. This ADR historically extended that destination to the Postgres
cluster. The operator confirmed no account was available on 2026-10-12.
The operator also supplied the vendor's 2026-08-31 response withdrawing Zurich
for new contracts. Region and procurement therefore remain unresolved; another
region/provider is not silently substituted. See [readiness](../runbooks/RSYNC-NET-READINESS.md)
and the [2026-10-29 reconciliation](../infra/BACKUP-EVIDENCE-RECONCILIATION-2026-10-29.md).

## Decision

The accepted physical-backup/WAL direction below remains a design. The
2026-10-12 observation did not find it active. SFTP destination, procurement,
key custody, activation and isolated restore require their own evidence. The
historical logical dump is neither erased by this decision nor evidence that
these phases were executed.

Deploy **pgBackRest** on jaguar, one stanza (`jaguar`) covering the
whole cluster, in two phases:

**Local phase (runbook phases 1 to 3):** pgBackRest from the Ubuntu
24.04 archive (2.50+, SFTP-capable; no third-party apt repo), package
held after install. Continuous WAL archiving via
`archive_command = pgbackrest archive-push`, `archive_timeout = 60s`
(RPO in the minutes range). Weekly full + daily differential backups
to a local repository on jaguar (`/var/lib/pgbackrest`), retention 2
fulls. Everything encrypted with pgBackRest's AES-256-CBC repo cipher;
the passphrase lives in `pass` (`rbx/backup/pgbackrest-cipher`), is
read at deploy time on the operator machine and written only to the
postgres-owned 0600 config on jaguar (postgres must read it to
archive; a compromise of the postgres OS user therefore reads the
cipher key, which is the honest boundary of this design). The
passphrase must be added to the audit's Root of Recovery inventory as
the backup encryption key, replacing or complementing the placeholder
`age-backup` item, with the component's `encryption_key_ref` updated
accordingly; the two locations / two custodians invariant then applies
to it.

Deployment-order safety rule: the stanza is created **before** the
archiving drop-in is placed. stanza-create does not require archiving,
and this ordering guarantees that an unscheduled restart (a
postgresql-16 package upgrade postinst, the paradedb tuning handler, a
host reboot) activates a working archiver instead of a failing
`archive_command` that would accumulate WAL until the disk fills.

**Off-site phase (runbook phase 4; flag `pgbackrest_offsite_enabled`,
off until the rsync.net account exists):** second repository over SFTP
to rsync.net Zurich, same cipher (client-side; rsync.net stores
ciphertext only), retention 4 fulls, dedicated ed25519 key for jaguar
restricted to its own subdirectory, rsync.net host key pinned by
sha256 fingerprint, async WAL archiving with a local spool so an
off-site outage never stalls local archiving, and a dedicated backup
schedule per repository (pgBackRest backs up one repo per invocation).
Provider-side ZFS snapshots (7 daily, out of band of jaguar's
credentials) are the deletion/ransomware backstop.

Operational constraints, deliberate:

1. **The role never restarts PostgreSQL.** Enabling `archive_mode`
   requires a restart of the shared cluster (brief ZITADEL login and
   PDNS-write outage). The role drops the config and reports; the
   restart is a human-scheduled window, exactly like the max_connections
   change before it (see the tuning file's own note).
2. **Admission criterion for the off-site tier:** Tier 0 +
   non-regenerable + covered by a restore drill. Anything else needs a
   written justification. This keeps the premium off-site an auditable
   inventory of critical state, not a dump.
3. **Restore drills restore to bengal**, never to production, per the
   rbx-resilience drill specifications. The first passing drill is what
   turns the audit's postgres-zitadel record from gap into something
   demonstrable (fact remains gated by the audit's trust boundary).

This supersedes the still-unimplemented "daily pg_dump of pdns"
plan item in PLAN-dns-email-architecture.md: the stanza covers the
pdns database with a better RPO than the planned daily dump.

## Alternatives considered

- **WAL-G**: solid, S3-first; would fit a B2 target. pgBackRest wins
  on the full cycle (verify, retention, PITR, multi-repo) and native
  SFTP that matches rsync.net.
- **Scheduled pg_dump only**: RPO equals the dump interval; cannot
  meet a minutes-range RPO for identity. May be added later as an
  extra logical layer, not the base.
- **PGDG apt repo for a newer pgBackRest**: rejected; the Ubuntu
  archive version suffices (SFTP since 2.46) and adds no third-party
  trust anchor.
- **Streaming replica for availability**: out of scope. This ADR buys
  durability. HA for the shared cluster is a separate, later decision.

## Consequences

- jaguar gains local backup load (zstd compression, process-max
  capped) and, in phase 2, outbound SFTP traffic to Zurich.
- One PostgreSQL restart window is owed before WAL archiving starts.
- The audit's postgres-zitadel gaps for state.backup.target,
  frequency, encryption_key_ref and offsite become closable with
  facts; the restore drill and `--write-status` stay gated by the
  rbx-resilience trust boundary (docs/roadmap.md there, section
  "Trust boundary (2026-08-27)", which requires execution records,
  fact-gate hardening and auditor integrity before any real fact).
- Runbook: `docs/runbooks/PGBACKREST-BACKUP.md`.
