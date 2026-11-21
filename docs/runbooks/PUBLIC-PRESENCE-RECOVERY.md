# Public Presence recovery and first assisted cycle

Checkpoint: 2026-11-15, America/Sao_Paulo. Scope is the Public Presence service
and one assisted institutional Merovelis Systems LinkedIn cycle within 90 human
minutes per week. The initial source checkpoint on November 14 made no production
change. The controlled execution below reconstructs the held application's
registry access. Database activation, publication and receipt remain pending.

## Observed state and ownership

- Public Presence #21 is merged at `8643b56486cae9c3fed1e385d35d4cd912591a81`;
  FlightDeck #23 at `5387720d9401dca2838c712656511140b13bd62d`.
- Public Presence #22 is merged at `e394c539bdd6fee81ae38c9e63e189f4e1cc9773`;
  its CI and image-publication workflow passed. The old promotion self-push
  failure is replaced by immutable pins for a reviewed infra PR.
- Authenticated read-only manifest requests returned HTTP 200 for both selected
  images. Their exact digests are pinned in the held overlay. No token was
  renewed or permission expanded.
- Initially the legacy migration waited on image pull, with API/web unavailable.
  Restoring its missing pull Secret alone could release the old hook. The merged
  source declares manual sync, suspended migration and disabled publication
  worker. The execution below reconciles the hold before credential recovery.
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

## Executed held-access recovery, 2026-11-15

The operator merged infra #399 as
`3ec258863f8621b2bfba5e5058fac4eedc8bce96` and requested continuation. Its
manifest verification and both backup-image build/scan/publication workflows
passed. Published preservation tooling is not a production backup receipt.

The [bounded preflight](evidence/public-presence-recovery-2026-11-15/held-access-preflight.json)
was checkpointed remotely before execution. This phase reconstructs only
Git/pass-derived control configuration and an ephemeral probe. It neither
qualifies the separately owned GPG custody nor releases persistent application
state without the actual external backup/restore handoff.

The [live result](evidence/public-presence-recovery-2026-11-15/held-access-result.json)
records:

1. Selective root reconciliation of only `argocd/rbx-public-presence`, without
   prune. The actual resolved revision matched the merged #399 commit. An initial
   explicit-SHA sync was refused because the root follows `main` automatically;
   canonical-main selective sync succeeded and its resolved SHA was verified.
2. Manual sync on the child Application, termination of its old operation and
   suspension of the migration before restoring registry access. ArgoCD later
   removed the terminated hook. At final inspection the migration Job and its
   pods were absent; the canonical migration manifest remains suspended.
3. Dedicated Ansible check mode passed with zero changes. Execution reconstructed
   only `rbx-public-presence/ghcr-pull-secret`, using the existing credential;
   one change, no rotation and no credential values in output.
4. Probe Job `rbx-public-presence-image-check-ww5mv` completed on node `tiger`
   with exit 0 and exact API imageID
   `sha256:d12b9b199b89c91eb2535b56d4c57a78260526bd0eca5a1a06746c4781c629ea`.
   It used `Always` pull, no database credentials or service-account token, and
   ran only `doctrine-check`. The probe has a 3,600-second post-completion TTL.

This proves live API image access and runtime doctrine validation. Authenticated
registry reads also returned 200 for the selected web manifest; a web deployment
is not claimed. The service still has zero API/web Deployments, its public root
returns HTTP 503, and its database still has zero application tables. No migration
or publication worker was released.

The [assisted-cycle readiness record](evidence/public-presence-recovery-2026-11-15/assisted-cycle-readiness.json)
records renewed LinkedIn admin-surface access to organization `103949050`. Its
remote label remains RBX Systems; the institution's owner-confirmed current name
is Merovelis Systems. Five visible posts were checked, including the distinct
September promotion of the source article. The new pilot draft was not seen in
that bounded range; reconcile history again immediately before any publish.

The frozen single-post hash remains unchanged. A read-only owner-scoped Flight
Deck provisioning plan returned `operation: create`, `changed: false`, one
90-minute shared ceiling and no Workstream ID yet. Owner references were resolved
from the current allowlisted OWNER inventory and retained only as reproducible
private metadata, without printing email or credential values. The source tool
was the frozen #24 head; GitHub still reported that PR OPEN at inspection despite
the operator's merge report. No real Workstream, approval, Action, publication
receipt or retrospective was created by these checks. Actual human minutes
remain unmeasured, not zero.

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
State alterado: held Application control configuration, terminated/suspended legacy hook, one namespaced pull Secret and one ephemeral doctrine-only probe; no application database mutation or publication
Fonte de verdade: Public Presence migrations/doctrine, infra GitOps and controlled Ansible; real databases remain on jaguar
Backend remoto: canonical Git repositories and private encrypted password store; external production backup remains with the other session, not qualified here
Backup e última execução: previous encrypted empty-DB cache is not an external backup; no real external production backup verified
Último restore testado: synthetic Public Presence/Comms passed; real bootstrap decryption failed NO_SECKEY; live configuration reconstruction/image pull now passed, not a data restore
RPO/RTO: service targets proposed 24h/4h, no proven full-service recovery guarantee
IaC/commit: executed merged infra #399 at 3ec258863f8621b2bfba5e5058fac4eedc8bce96; existing reviewed API/web digest pins retained
Drift encontrado: registry access recovered and stale operation stopped; API/web still absent with HTTP 503, data migration held, real Journal Workstream absent and Flight Deck #24 observed OPEN
Estado local residual: reproducible worktrees/caches, prior encrypted bootstrap artifact and privately cached owner-reference metadata; no new canonical local state or plaintext production database/key export
Riscos ou bloqueios: qualify the other owner's external backup/restore handoff, confirm actual #24 merge, then reviewed service activation, owner-scoped provisioning, exact-version approval, publication receipt and retrospective
```

Voice: R4 with R6 overlay, RBX Voice System v0.1. Authored with Codex.
