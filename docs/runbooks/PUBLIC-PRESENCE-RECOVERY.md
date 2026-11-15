# Public Presence recovery and first assisted cycle

Checkpoint: 2026-11-14, America/Sao_Paulo. Scope is the Public Presence service
and one assisted institutional Merovelis Systems LinkedIn cycle within 90 human
minutes per week. No production migration, deployment, secret mutation,
publication or receipt is claimed by this source checkpoint.

## Observed state and ownership

- Public Presence #21 is merged at `8643b56486cae9c3fed1e385d35d4cd912591a81`;
  FlightDeck #23 at `5387720d9401dca2838c712656511140b13bd62d`.
- The latest Public Presence CI and image-build jobs passed. Its promotion job
  failed after the images were published. The companion change removes the
  protected-branch self-push and emits immutable pins for an infra PR instead.
- Authenticated read-only manifest requests returned HTTP 200 for both selected
  images. Their exact digests are pinned in the held overlay. No token was
  renewed or permission expanded.
- The legacy migration still waits on image pull, with its API/web unavailable.
  Restoring its missing pull Secret alone can release the old hook. Source
  configuration now proposes a manual-sync hold, suspended migration and disabled
  publication worker. These guards are not applied merely by committing them.
- The current `rbx-presence` source bucket listed zero objects and zero bytes,
  with no truncation in the current-object listing; historical versions were
  not enumerated. The first pilot is text only. Recheck before activation;
  do not interpret this snapshot as protection for later media uploads.
- The operator assigned external backup storage and GPG recovery custody to
  another session. Do not create competing keys/buckets or claim that dependency
  is complete. Integration needs its actual destination, scoped credential and
  usable encryption/recovery material references when handed over.

The encrypted GHCR credential matches its private password-store remote byte
for byte. Registry access can reuse it. The exact source reference remains
`rbx/cluster/ghcr-token`; credential values never belong in this runbook.

## 1. Hold the existing application before restoring registry access

Merge the recovery overlay first. Reconcile the Application's manual-sync
setting through its app-of-apps owner, then terminate the stale operation using
the authorized ArgoCD procedure. Applying the Application spec does not itself
terminate an already-running sync. Suspend the existing migration hook to match
the reviewed desired state and wait for nonterminal pods to disappear.

Do not delete the database, rebuild its role/password, remove assets, enable
the worker or run a full fleet secret bootstrap. The intended runtime change at
this phase is one held application and one namespaced image-pull Secret.

The dedicated procedure uses the canonical Ansible defaults and verifies the
production cluster UID before reading a token:

```sh
ansible-playbook -i localhost, bootstrap/ansible/public-presence-registry.yml \
  -e kubeconfig_path=/path/to/the/approved/kubeconfig --check
```

Replace the kubeconfig reference with the existing approved local path. A
check-mode run performs only metadata reads and never decrypts `pass` or applies
a Secret. Execution without `--check` is within the separately authorized
recovery operation after the guards pass. The actual playbook refuses automatic
sync, a running sync operation, an unsuspended/active migration or a remaining
nonterminal migration pod. It writes only `rbx-public-presence/ghcr-pull-secret`
through stdin with `no_log`; it does not pass a password in process arguments.

The metadata guards do not qualify backup evidence or authorize migration.
They keep credential recovery from releasing the old hook. A new live apply
must still pass the state/recovery preflight for its actual affected state.

## 2. Prove image pull separately from database execution

After the held registry operation, create the bounded probe from
`bootstrap/manifests/public-presence-image-check.yml` once and retain the returned
Job identity. It pulls the selected digest with `Always`, has no service-account
token or application credentials, and runs only `doctrine-check`. It does not
load config, connect to PostgreSQL, synchronize profile records or publish.

Require Job completion and resolve the pod imageID against the selected digest
or its verified node-platform manifest. An OCI index digest and its platform
manifest digest can differ; retain the resolved relationship in the evidence.
A manifest read, cached image or job creation by itself is not this proof. The
probe is ephemeral and is not included as a recurring GitOps workload.

## 3. Integrate service preservation with the external backup owner

The existing PostgreSQL preservation image gains an explicit Public Presence
entry point. Comms remains the default; its production digest pin is unchanged.
The new path admits only `public_presence` and `public-presence/backups/`, requires
an explicit destination and credentials, and retains the existing private-bucket,
versioned readback, integrity, encryption, bounded execution and cleanup checks.
No new backend has been silently defaulted to the workload's S3 credentials.

The producer receives a public certificate and fingerprint only. The database
snapshot is read-only; unrelated environment secrets do not reach child tools.
An upload receipt has `restore_tested_at: null` until a separate restore proves
the exact captured artifact. Source assets have a separate preservation scope.

Use the main-only, scanned image artifact published by the existing backup-image
workflow after this change merges. Verify its real registry digest before a
separate GitOps CronJob pin. Do not use the current old image's default Comms
entry point for the Public Presence database or invent a digest. Configure the
job to execute `python3 /app/presence/backup_runner.py`.

Initial operating proposal: daily snapshot at 03:45 America/Sao_Paulo, 1 GiB
maximum per snapshot, RPO 24 hours and RTO 4 hours. These are proposed objectives,
not measured service guarantees. Coordinate retention, credential capabilities,
encryption, monitoring and restore evidence with the external-storage owner;
no object deletion or retention-policy mutation is introduced here. The backend
must support the checks required by the shared archive adapter. An unsupported
policy/ACL/versioning check fails closed; do not bypass it to claim readiness.

Require a real external receipt and isolated restore, then a first successful
scheduled run and freshness/failure monitoring before calling recurring
preservation operational. Preserve opaque destination/key references privately.

## 4. Bootstrap restore drill and current result

The reproducible `scripts/presence/bootstrap_restore_drill.py` is restricted to
an empty bootstrap database. It checks the absence of application tables before
and after a read-only SSH snapshot. If the database is no longer empty it stops;
use the normal full preservation path rather than capturing unreviewed data.

The tool keeps plaintext in memory and an isolated, network-disabled,
tmpfs-backed PostgreSQL container. It exports no private key and mounts no source
credentials/keyring in that container. Its encrypted cache and metadata receipt
are not an external backup or an independent-key custody drill.

The first real attempt captured the empty source read-only and produced an
encrypted archive, but local GPG decryption reported `NO_SECKEY`. No PostgreSQL
restore container was started and no production state changed. This failed
attempt remains failed. Re-run as a new attempt with the operational recipient
and usable recovery material provided by the other session; do not promote the
synthetic result below into a production restore pass.

Separate synthetic exercises succeeded for Comms and Public Presence in the
newly built preservation image, with the fixture mounted read-only. The Public Presence case
restored linked content, version and receipt rows, verified GPG/manifest/dump
hashes and logical state, and cleaned its private temporary data. Cloud transport
was stubbed. These exercises prove implementation behavior, not a production
archive, independent custody or a complete service RTO.

## 5. Release service recovery, then execute the first cycle

1. Recheck the live database and source bucket. Obtain the actual external
   backup/restore evidence and reviewed operation scope. Keep the failed attempt
   and any new success as separate evidence records.
2. Release the migration in a reviewed GitOps revision and perform a controlled
   sync with the selected images. Keep `PRESENCE_WORKER=off` initially. Verify
   actual API/web ready endpoints, health/readiness and the authenticated console.
3. Inspect actual doctrine/profile UUIDs and the complete existing outbox before
   enabling publication. Do not process unrelated queued work. Source doctrine
   binding and an image doctrine check are not runtime record IDs.
4. Bind or provision the real FlightDeck Workstream/cycle through its owner path.
   Never run the disposable integration fixture against production. Select one
   published Journal article and one institutional LinkedIn placement, reserving
   the shared 90-minute ceiling.
5. Import only the frozen single placement with real owner references. Obtain
   approval of its exact version under `human_required`; then use the existing
   manual Publication/HandoffTask path. Release the normal worker only after its
   scope and outbox are reconciled.
6. Publish once to organization `103949050`, reconcile remote history, and record
   the real permalink/receipt. Observe available 24h/7d metrics and actual human
   minutes, then save the FlightDeck retrospective. No first-cycle receipt or
   commercial result exists at this checkpoint.

## Recovery report

```text
State alterado: reproducible source, synthetic isolated tests and encrypted bootstrap cache; no production mutation
Fonte de verdade: Public Presence migrations/doctrine, infra GitOps and controlled Ansible; real DB remains on jaguar
Backend remoto: existing private repositories; external production backup destination owned by the other session, not qualified here
Backup e última execução: encrypted empty-DB cache captured read-only; no external production backup verified
Último restore testado: synthetic Public Presence/Comms passed; real bootstrap decryption failed NO_SECKEY, restore not started
RPO/RTO: proposed 24h/4h, not a proven service objective
IaC/commit: recovery feature branch and companion Public Presence PR; immutable image pins from the merged Public Presence #21
Drift encontrado: absent pull Secret, stale migration image, running hook, no API/web; promotion self-push failure after successful build
Estado local residual: reproducible worktrees/caches and encrypted bootstrap artifact; no plaintext production database or private key file on the host
Riscos ou bloqueios: merge/reconcile the held overlay, integrate external backend/key handoff and pass the actual restore before releasing persistent publication state
```

Voice: R4 with R6 overlay, RBX Voice System v0.1. Authored with Codex.
