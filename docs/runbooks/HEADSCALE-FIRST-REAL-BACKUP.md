# First real Headscale backup: operation contract

Prepared: 2026-11-08. Status: **preparation only; real capture, upload, decryption
and restore are blocked** pending the current baseline, recovery custody and
production archive scope below. No production operation is implemented or
authorized by publishing this runbook. No live result is asserted here.

The next deliverable is one bounded, encrypted Headscale export and a verified
restore outside Contabo. This is one part of H1, which also requires an explicitly
selected PostgreSQL database. Object files/history, mail, other databases and
the rest of the host are separate scopes. An initial real restore does not prove
scheduled freshness, the proposed one-hour/four-hour H1 targets or repeatability.

## Evidence and present unknowns

The [canonical recovery skill](https://github.com/rbxrobotica/rbx-agent-layer/blob/main/skills/rbx-state-backup-iac-recovery/SKILL.md)
requires: "If any required field is absent, unknown, stale, or exclusively local,
stop before mutation and present a remediation plan." Its fields are affected
state, source of truth, remote location, backup, tested restore, RPO/RTO, owner
and local-only risk. Reproducible source preparation and metadata inventory can
proceed; unknown recovery fields are not permission to capture production data.

| Evidence | Established fact | Does not establish |
| --- | --- | --- |
| [October 12 reconciliation](HEADSCALE-RECONCILIATION.md) | At that time Eagle ran Headscale 0.29.3, SQLite, file-backed policy and public DERP maps; a local daily backup timer and archive metadata were observed | Current configuration, current archive contents/freshness, external custody or an operational restore |
| [Candidate preparation](HEADSCALE-CANDIDATE.md) | Reconstructed original configuration/policy hashes and isolated synthetic validation | A fresh source snapshot, current in-memory policy or recovery of real identities |
| Recovered Git commit `4f84a1a0d2f5526e3d1c825baaa75f38cf030ef0` | Historical backup/restore templates describe SQLite online backup and local archives | Deployed helper equivalence, multi-file atomicity, encryption or usable external backups |
| [Synthetic B2 protocol](RESILIENCE-B2-CANARY.md) | Reviewed bounded fixture mechanism and its offline tests | A production producer credential, current source capture, independent recovery custody or H1 |
| November 8 metadata observation | Headscale active/running/enabled; installed package 0.29.3; fixed config parsed and its hash matched the October 12 receipt; local state/backup metadata and runtime linkage observed | Payload contents, consistent capture time, current restore, effective config/environment selection, installed-binary equivalence or independent custody |

The legacy role is quarantined. Do not reapply it or the recovered rollout: they
can replace the live policy. The original ADR's "DNS change plus one SQLite
file copy" is not a complete recovery procedure for configuration and keys.

## Read-only SSH inventory

[`headscale_inventory.py`](../../scripts/resilience/headscale_inventory.py) is a
bounded observer, not a source capture or backup. It reads only the fixed
`/etc/headscale/config.yaml` into memory, emits its hash and whitelisted path
references/flags, and collects metadata without opening the database, policy,
private-key, certificate, cache, script or archive contents. It does not prove
that the active process selected this fixed config; unit/drop-in/environment
references identify dependencies for later private reconciliation.

The shared tree limit is 256 attempted entries and depth three. Subprocesses
select only approved `systemctl show` fields and package versions; no Headscale,
SQLite or GPG program is invoked. Config values, users, hostnames, URLs and
unexpected filenames are redacted. PyYAML must already exist on the target;
missing parsing support is reported, never installed automatically.

After an existing SSH route and its host key have been independently confirmed,
an operator may use the following from the repository root. It preserves strict
host-key checks, disables agent/port forwarding and connection multiplexing,
and streams reviewed source through stdin without writing a remote script.
It uses the configured identity/agent; an empty agent or missing key is a blocked
route, not permission to replace host keys, copy credentials or relax checking.

```bash
set -euo pipefail
umask 077
inventory_dir=$(mktemp -d "${TMPDIR:-/tmp}/rbx-headscale-inventory.XXXXXXXX")
chmod 0700 "$inventory_dir"
date -u +%Y-%m-%dT%H:%M:%SZ > "$inventory_dir/started-utc.txt"
sha256sum scripts/resilience/headscale_inventory.py > "$inventory_dir/observer-sha256.txt"
timeout --kill-after=5s 50s ssh -T \
  -o BatchMode=yes -o StrictHostKeyChecking=yes -o UpdateHostKeys=no \
  -o ForwardAgent=no -o ClearAllForwardings=yes -o RequestTTY=no \
  -o ControlMaster=no -o ControlPath=none \
  -o ConnectTimeout=10 -o ConnectionAttempts=1 \
  -o ServerAliveInterval=5 -o ServerAliveCountMax=1 \
  eagle 'sudo -n /usr/bin/python3 -I -B -' \
  < scripts/resilience/headscale_inventory.py \
  > "$inventory_dir/observation.json" 2> "$inventory_dir/transport.stderr"
```

The receipt and transport diagnostics stay private (directory 0700, files 0600)
and outside Git. A failed transport, malformed/incomplete JSON, `parsed:false`,
missing metadata or truncation is an unresolved observation, not proof that the
source or backups are absent. Inspect the dated receipt privately, reconcile
unknown paths and move it into the encrypted private register; do not publish
the full observation. No real capture, upload, retention change or restore is
authorized by this invocation.

Validation on 2026-11-08: the immutable network-none rootless fixture ran 61 tests
with **60 passed and one explicit skip**, because that image lacks PyYAML; the
skipped test is the adversarial YAML parser test. The additional synthetic
GPG/SQLite round trip passed. All eight inventory tests passed locally with
PyYAML. The existing `validate-yaml` CI job now has an explicit `import yaml`
guard followed by these eight tests, using its already configured dependency;
publishing this step does not assert that remote CI has run. No image or
dependency installation was changed for this work.

## Required inventory and private manifest

Keep detailed paths, identifiers, hashes of sensitive inputs, state manifests
and raw observations in an encrypted private register outside Git. Publish only
reviewed aggregates and non-sensitive references. Never display a database,
key, password-store entry, process environment or complete service unit.

Inventory the effective process/configuration selection, unit and drop-in
references, environment-file references, installed version and pinned package
provenance. Metadata observation must not invoke `headscale configtest`, open
the database, traverse secret contents or run the existing backup helper.

| Required class | Expected historical reference; current verification required |
| --- | --- |
| Configuration and policy | `/etc/headscale/config.yaml`; resolve the effective `policy.mode` and policy path, preserving original bytes |
| Database | Historically `/var/lib/headscale/db.sqlite`, SQLite/WAL; the export is an online-backup snapshot, not a raw live database/WAL copy |
| Server identity | Effective Noise private-key path; never silently generate a replacement during verification |
| Conditional state | Embedded DERP key/local maps if required by the effective profile; ACME cache/account/certificate state and any configured external TLS files |
| Runtime and bootstrap | Exact Headscale version/package hash, compatible OS/runtime, service identity/permissions, unit/drop-in/environment references and separately recoverable package/code copies |
| Recovery dependencies | Archive read identity, decryption material, trusted receipt/authentication material, independent account/MFA recovery, resolver and offline instructions |

Classify every discovered path as included, excluded with a reason, conditional
or unknown. Sockets, PID files and SQLite live WAL/SHM are not substitutes for a
database snapshot. Reject unexplained paths, symlinks, devices and file changes;
define finite file/tree counts, depth, per-file/total sizes and deadlines before
opening source payloads. Do not use an unbounded `tar` or `cp -a` of host state.

The following is a **private planning template**, not a validated schema or a
command input. Null values mean blocked; add the required per-file manifest and
state-verification specification privately before approval.

```yaml
schema: rbx.headscale.first-real-backup-plan.v1
status: blocked
run_ref: null
owner_ref: null
source:
  host_ref: null
  observation_at_utc: null
  observation_receipt_ref: null
  acceptable_observation_age_seconds: null
  effective_config_ref: null
  effective_policy_ref: null
  effective_database_ref: null
  required_identity_and_tls_file_refs: null
  unit_dropin_and_environment_refs: null
  version_and_package_provenance_ref: null
  source_manifest_ref: null
consistency:
  method: null
  writer_coordination_and_window_ref: null
  sqlite_snapshot_start_utc: null
  sqlite_snapshot_end_utc: null
  non_database_fingerprints_and_checks_ref: null
  snapshot_verification_queries_ref: null
bounds:
  files_depth_and_sizes_ref: null
  total_capture_bytes: null
  execution_deadline_seconds: null
archive:
  provider_account_bucket_region_ref: null
  exact_unique_export_prefix_ref: null
  producer_capabilities_and_expiry_ref: null
  separate_recovery_identity_ref: null
  closed_export_retention_and_cost_ref: null
encryption_and_recovery:
  public_recipient_fingerprint_ref: null
  private_key_and_offline_custody_refs: null
  trusted_receipt_or_authenticated_manifest_ref: null
  independent_access_and_open_test_ref: null
  clean_restore_runtime_and_isolation_ref: null
  verification_and_cleanup_plan_ref: null
objectives:
  capture_scope_rpo_rto_ref: null
  success_age_monitoring_and_retention_ref: null
authorization:
  exact_operation_tuple_ref: null
  current_operator_authorization_ref: null
```

For each payload item record private logical name, source reference, role,
regular-file type, owner/group/mode, size, SHA-256, capture times and inclusion
reason. The database manifest includes schema/version and predeclared
deterministic checks over the captured snapshot: identities/relationships,
relevant state, integrity and policy/key consistency. Expected results must
come from that same snapshot or an independently declared captured-copy
procedure, not a later query against the mutable live service. Do not publish
node identities, addresses, credentials, tokens, policies or query results.

## Consistent capture contract — NOT IMPLEMENTED

1. Reconcile the current metadata baseline and all paths against the approved
   inventory. Missing or changed references stop the run before payload access.
2. Coordinate a defined window with no administrative configuration/policy/key
   changes or certificate rotation affecting the included files, or demonstrate
   an equivalent coherent snapshot method. Identify every relevant writer and
   how the window is enforced and verified. A capture-process lock does not
   lock those writers. Do not stop/restart/pause a service under inventory-only
   authorization; any required interruption is a separate reviewed operation.
3. Use SQLite's online-backup API to a new private destination with finite
   busy/time/size bounds. Do not manually copy a running database plus its WAL,
   checkpoint/prune the live WAL or use immutable mode on an active source.
   SQLite consistency does not make the other captured files atomic with it.
4. Read only the approved, bounded regular files; preserve original bytes and
   manifest their metadata. Recheck fingerprints/hashes and the coordination
   window around capture. These rechecks detect observed changes; alone they
   do not prove cross-file atomicity or exclude change-and-revert races.
5. Verify the snapshot and manifest before encryption. Record the start/end
   interval honestly; absent a stronger source-time proof, use its conservative
   start for recovery-point calculations rather than upload completion time.
6. Encrypt the closed export to a separately verified public recipient. The
   producer must have no private decryption key. Do not decrypt/export password
   store secrets for backup work. Required service-owned identity state must
   remain private and leave the source only within the encrypted payload; the
   capture/staging implementation and cleanup must satisfy the recovery policy.
   If a required secret cannot be handled under that policy, the gate remains
   blocked. Unlinking temporary plaintext is not secure erasure.

No executable production capture command is supplied here. The historical
helper, synthetic `local_fixture.py` and synthetic canary are not substitutes.
Implement and review the bounded capture/manifest/encryption tool against
fixtures before supplying an exact source operation command.

The separate [synthetic capture/restore mechanism](HEADSCALE-CAPTURE-FIXTURE.md)
now exercises WAL-aware snapshots, required dummy identity/configuration files,
bounded manifests and encrypted local recovery. It accepts no real input and
uses neither the Headscale database schema nor its executable. It is preparation
for this contract, not its production capture or native recovery implementation.

## Archive and independent recovery gates

- Qualify the actual production producer and separate recovery identities,
  scopes, expiry/rotation and retention protection on a disposable equivalent
  configuration. The temporary 25-capability synthetic actor is not a production
  identity. Verify the producer cannot defeat the required protection, including
  applicable retention/bypass/policy paths; do not test deletion on real exports.
- Keep the existing shared S3 clients and their `GetBucketPolicy` checks intact.
  A separate Native backend must pass its own privacy, scope, version, retry,
  pagination, bounded readback and error contracts. Do not silently fall back to
  Contabo or repurpose the metadata-only simulation as a live qualification.
- Select the actual archive account/bucket/region, unique reference, retention,
  lifecycle, size/cost ceiling and monitoring before upload. Apply default lock
  only to closed encrypted exports; mutable Restic/pgBackRest repository files
  need their own compatibility proof. Never infer protection from Object Lock
  being enabled without a verified object retention result.
- Verify recovery access, private-key decryptability and trusted receipt or
  authenticated manifest outside the producer's control. Hashes held beside
  ciphertext by the same writer do not establish provenance or freshness.
  References in `pass` are not proof of a private remote or offline GPG custody.
  Record independently usable locations/custodians and their open tests; a
  founder-only exercise leaves the second-custodian requirement unqualified.

## First real restore and acceptance — NOT IMPLEMENTED

After the preceding gates and exact authorization pass, capture/upload once,
retain its private receipt and independently download the exact archive version.
Verify byte count/hash and authentication/provenance before decryption. Restore
using the qualified recovery identity/key without source credentials, source
mounts, password-store mounts or source access.

Restore into new private disposable storage on a clean host outside Contabo.
Keep the original restored files unchanged for verification; any native startup
uses a separate disposable working copy, exact compatible Headscale/runtime and
network disabled. Mount no live sockets, keys or databases and publish no ports.
Do not enroll clients, alter public DNS, request ACME certificates or contact
production peers. Predeclare the runtime test profile and every bounded
sandbox-only difference; preserve its relationship to the original configuration.

Verify manifest/files, SQLite integrity and the declared snapshot queries,
identity/key continuity, policy acceptance and isolated process health. Native
`configtest` can create keys or migrate a database; a zero exit alone is not
functional recovery. Record which checks are structural, native or functional,
and which TLS/DERP/client behavior remains untested. The existing native fixture
accepts synthetic inputs only and must not be pointed at real operational data.

Record executor, source capture interval, archive version receipt, trusted
manifest, runtime/source revisions, actual interventions, costs, cleanup and
measured recovery duration. Keep source services and retained real archives
intact. Review scheduling/overdue notifications, retention and a second complete
recovery separately before claiming the full H1 acceptance criteria.

## Exact operation approval tuple

Once all references are populated and verified, present one concrete tuple:
`run + source host/baseline + allowlisted state + consistency window/method +
capture tool hash/version + recipient fingerprint reference + producer and
recovery scopes + archive account/bucket/region/prefix + byte/time/cost limits +
retention/lifecycle + trusted receipt/custody + isolated restore checks + cleanup`.
Record current operator authorization for that tuple. General interest in
backups, a successful synthetic run or approval of this document does not fill
the unresolved fields or authorize an unrelated production operation.

## Current handoff

Source/runbook preparation and the bounded read-only metadata observation are
complete for this increment. On November 8 the frozen observer ran through the
existing authenticated route without a server change. Headscale was
active/running/enabled and the installed package was 0.29.3. The fixed config
parsed and its hash matched the October 12 receipt. Embedded DERP was disabled;
there was no DERP private-key reference or OIDC section in that config.

Required-path metadata showed an 81,920-byte database, a 72-byte Noise key, a
234-byte policy and an existing cache directory. The bounded state inventory
observed seven regular files totaling 683,637 bytes. The backup directory had
15 regular files totaling 246,357 bytes; its newest file mtime was
2026-11-08T03:24:00.825204Z. The `rbx-headscale-backup` timer was
active/enabled/waiting; the oneshot reported result success and status zero.
These are metadata observations: no database, key, policy, cache, script or
archive payload was inspected and no restore ran. File mtime and service success
do not establish consistent capture time, usable backup contents or RPO.

Runtime linkage identified the expected binary with `serve` and no explicit
config flag. Effective configuration/environment selection remains unproved.
The cached package hash matched the repository's 0.29.3 pin; that does not
establish equivalence of the installed executable. Detailed observation receipts
remain private outside Git.

The complete private inventory, consistency contract, encryption/recovery
custody, production producer/recovery identities and real capture/upload/restore
implementations remain pending and blocked as declared above. No production
capture, archive upload or service change is authorized by this metadata result.
Preserve the reviewed source and sanitized findings in the authorized feature
branch/PR checkpoint before operational handoff. Never publish the private
manifest, operational files or recovery material.
