# DNS IaC state custody before production rollout

Date: 2026-11-15. This is a gated preparation runbook. No state migration,
import, force-unlock, production plan or apply has been executed by this work.

## Established boundary

Canonical infra main `3ec258863f8621b2bfba5e5058fac4eedc8bce96` explicitly
declares a local DNS state backend. A metadata-only inspection of ThinkCentre
found a principal DNS state and an older headscale-worktree state with different
sizes/checksums, plus their local backup files. No engine process or lock file
was observed at that instant. Recency does not establish canonical ownership.
The exact paths and checksums are retained in private resilience PR11 under
`evidence/2026-11-15-dns-production-qualification/state-metadata-thinkcentre.json`.

Do not print or copy state to a chat, Git, a review or a plaintext transfer.
Until a qualified copy and owner are identified, inspect only path/workspace,
size, modification time, checksum and lock ownership. Leave every discovered
state, backup and lock untouched. Coordinate with any existing operator/task;
an idle process snapshot is not a change freeze.

## Required evidence before migration

1. Accountable owner and authoritative working copy established; divergent
   copies inventoried without ad-hoc JSON merging. Source checkout and runtime
   resource ownership reconciled in a controlled review.
2. Current encrypted state backup produced at its source, stored outside the
   workstation/workload failure domain, and restored in a controlled validation
   that does not expose plaintext or change live resources. Record the source
   checksum, ciphertext destination, encryption-key inventory reference,
   retention, restore result and latest backup timestamp privately.
3. Backend and recovery copy qualified: TLS, encryption, version history,
   exclusive locking, least-privilege access and independently recoverable key
   custody. Record actual recovery RPO/RTO and accountable operation runbook.
4. Conditional-write race and unlock behavior exercised on disposable backend
   objects by independent clients. One lock owner must win; the other must fail
   without overwriting it. A WORM/retention policy must not prevent normal
   lock release. Object versioning/history and old-version restore must be
   tested separately. A supplier's S3-compatible label is not sufficient.
5. Exact migration plan reviewed against the pinned source state; explicit
   authorization for that state operation obtained. Preserve the pre-migration
   backup and never run `-lock=false` or force-unlock an unidentified owner.

The existing B2 account is a candidate for evaluation, not a qualified backend.
Do not repurpose the immutable canary-backup bucket or assume it provides the
conditional writes required by S3 locking. The inert example in
`experiments/dns-production/backend-s3.tf.example` documents required backend
controls without activating a new backend or choosing a supplier/bucket.

## After the controlled migration

Prove remote read/write/locking and restore using a second controlled client,
then reconcile the production plan against the existing authority inventory.
Stop on unexpected deletion, zone replacement or drift. Preserve original
DNSSEC key/DS identities. In particular, the PowerDNS provider's zone creation
`nameservers` argument is not a safe shortcut for editing ordinary NS records:
the existing source warns that changing it can replace the whole zone.

Only after state custody, actual production content/key recovery, account
protection, transfer compatibility and independent monitor/failure drills qualify
should a delegated disposable canary and exact per-zone cutover be proposed.
The prior NSD public-snapshot restore and origin restart do not restore the
production PowerDNS PostgreSQL database or its private signing keys.

Sources:

- [OpenTofu S3 native locking](https://opentofu.org/docs/v1.10/language/settings/backends/s3/)
- [OpenTofu state locking](https://opentofu.org/docs/language/state/locking/)
- Existing mandatory RBX `rbx-state-backup-iac-recovery` skill and current
  `AGENTS.md` exact-operation production authorization policy.
