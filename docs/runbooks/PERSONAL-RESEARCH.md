# Personal research collector: activation and recovery

Status: proposed, manual ArgoCD sync, CronJob suspended. No production database,
Secret, registry package or Kubernetes state was changed to prepare this slice.
The public overlay contains scheduling and access contracts only. The private
image contains the collector and its approved configuration.

## State and recovery gate

Before any production operation, record the owner, exact approval, affected
state, current backup and restore evidence in the private operational register.
AGENTS.md requires exact current-message approval for merge, publication,
provisioning, Secret changes, migration and sync. The state recovery skill also
requires complete recovery evidence; authorization alone does not waive it.

Use the existing operator access path. Establish Jaguar's SSH host key through
the trusted inventory before connecting. Do not disable host-key verification,
substitute a broader identity after an RBAC denial, or print credentials.

Affected state and desired sources:

| State | Source | Recovery prerequisite |
|---|---|---|
| Collector code and config | Private application repository and immutable image | Clone/build and restricted package pull verified |
| Kubernetes configuration | This GitOps repository | Encrypted off-cluster control-plane snapshot with tested restore |
| PostgreSQL data | Dedicated career_watch DB on Jaguar | pgBackRest stanza jaguar, encrypted off-host backup and tested restore |
| Credentials and CA trust | pass under rbx/personal-research | Private encrypted remote and separate offline GPG recovery key |
| Reports | Rebuilt from PostgreSQL | No PVC or local-file dependency |

Backup configuration is not health evidence. The pgBackRest role defaults have
off-site disabled; do not infer the live value. Before provisioning, record
`pgbackrest --stanza=jaguar --output=json info`, the most recent successful
off-host backup and WAL check, encryption and retention, age monitoring and a
restore to an isolated destination. Use the existing backup runbook. S3 can be
the governed off-site destination if approved; the collector itself needs no S3
credentials. An on-host dump alone does not satisfy this gate.

Proposed objectives for this small research workload: RPO 24h, RTO 2h, owner the
application operator. Confirm these objectives and backup retention before
activation. The isolated application restore test is supplementary and does not
prove Jaguar or cluster recovery.

## Release and immutable image

Build the private application's reviewed commit with its Dockerfile and
`VCS_REF` set to that full commit. Run its PostgreSQL verifier first. Publish
only to the private package `ghcr.io/rbxrobotica/personal-research`, using a
commit tag `sha-<commit>`; verify package visibility and pull access explicitly.
Publication is a separate authorized operation.

Inspect the built image's revision label and CAREER_CODE_REVISION environment;
both must equal the full reviewed commit. Rebuild without cache if the local
builder reuses an older ARG value. Do not promote a mismatched image.

Replace `build-required` in `apps/prod/personal-research/kustomization.yml` with
the published tag and `digest: sha256:<verified registry manifest digest>`.
The release PR must retain `suspend: true`. Validate with:

```bash
python3 scripts/validate-personal-research.py
bash scripts/check-image-convention.sh apps/prod/personal-research/cronjob.yml apps/prod/personal-research/kustomization.yml
kubectl kustomize apps/prod/personal-research
ansible-playbook --syntax-check -i bootstrap/ansible/inventory/hosts.yml bootstrap/ansible/provision-personal-research-db.yml
ansible-playbook --syntax-check -i bootstrap/ansible/inventory/hosts.yml bootstrap/ansible/bootstrap-personal-research-secrets.yml
```

The Application has no automated sync. The app-of-apps may register it after
merge, but its first sync requires an operator operation. The new project
destination is limited to personal-research. ESO can read exactly two named
source Secrets in rbx-ia-br; the collector ServiceAccount has no API permissions
or mounted token.

## Provision and initialize

1. Complete and record the recovery gate above, including verified PostgreSQL
   TLS and a registry credential that can pull this private package.
2. Merge the reviewed app and infrastructure changes with the schedule held.
   Sync the personal-research Application to create inert resources. An
   ExternalSecret can be unready until the next step. No collector Job starts.
3. Create two distinct random passwords in pass, minimum 32 characters:
   `rbx/personal-research/owner-password` and
   `rbx/personal-research/runtime-password`. Keep them in the private encrypted
   remote, under the approved secret procedure.
4. Run the standalone DB playbook below on Jaguar. It creates only career_watch
   and the two dedicated non-superuser roles, with node-scoped TLS HBA entries
   ahead of broad host rules. It does not migrate application tables.
5. Prepare these pass entries without exposing values:
   `runtime-database-url`, `owner-database-url`, `postgres-ca-pem` and
   `registry-dockerconfig`, all under `rbx/personal-research/`.
   Both database URLs must target career_watch, use their corresponding role
   and password, and set `sslmode=verify-full`. Use the certificate's validated
   hostname; `hostaddr=161.97.147.76` can pin the Jaguar address when necessary.
   Mount the trusted CA at /etc/postgresql/ca.crt. Never weaken TLS to make a
   hostname mismatch pass. Confirm the configured registry is the org package.
6. Run the dedicated Secret bootstrap playbook using the existing authorized
   kubeconfig and the expected cluster UID from inventory. It uses server-side
   apply through stdin, no secret values in argv or last-applied annotations.
   Runtime and registry source records go to rbx-ia-br. The separate owner
   Secret goes only to personal-research and is not accessible by the runtime.
7. Wait for both ExternalSecrets to be Ready. Render the migration Job, review
   its image digest, owner-only environment and diff, then create it in the
   authorized operation. Wait for completion before a smoke collection.
8. Render a collect Job with a unique name, review and create it. Confirm the
   structured result in logs and that the runtime user cannot UPDATE, DELETE,
   CREATE or TRUNCATE. Render a report Job and verify a fresh report rebuild
   uses the stored observation time.
9. Record the successful smoke, backup/restore and digest in the private
   register. Submit the small activation change `suspend: false`, then sync it
   with exact approval. Revalidate the active image digest and next schedule.

Provision commands, from bootstrap/ansible, after their exact approvals:

```bash
ansible-playbook -i inventory/hosts.yml provision-personal-research-db.yml --limit jaguar -e personal_research_provision_authorized=true -e personal_research_preflight_ref=APPROVED_PRIVATE_RECORD
ansible-playbook -i inventory/hosts.yml bootstrap-personal-research-secrets.yml -e personal_research_secrets_authorized=true -e personal_research_preflight_ref=APPROVED_PRIVATE_RECORD -e personal_research_kubeconfig=EXISTING_AUTHORIZED_PATH
```

These flags record intent for a controlled invocation. They do not replace
human authorization or the recovery preflight. Neither playbook is imported by
site.yml, so an unrelated bootstrap cannot activate the collector.

## One-off Jobs and report access

The helper reads the current CronJob and writes a manifest. It does not submit
it, and refuses an image without a digest. Use a fresh DNS-safe Job name.

```bash
python3 scripts/personal-research-job.py migrate research-migrate-v1 > /PRIVATE/migrate.json
python3 scripts/personal-research-job.py collect research-smoke-v1 > /PRIVATE/smoke.json
python3 scripts/personal-research-job.py report research-report-v1 > /PRIVATE/report.json
```

Review each output before `kubectl create -f` in its authorized operation.
Wait with `kubectl -n personal-research wait --for=condition=complete
job/JOB_NAME --timeout=600s`. Read the collector's JSON receipt using
`kubectl -n personal-research logs job/JOB_NAME`.

A report Job emits private HTML, without another public-source collection.
Redirect its logs into a private local file, then open that file. The read Job
uses only the runtime credential. HTML and logs are derived data, never the
source of truth, and are not committed to this public repository.

Runtime status distinguishes COMPLETE, PARTIAL, FAILED, ALREADY_STORED,
ALREADY_RUNNING, PILOT_LIMIT_REACHED and PILOT_EXPIRED. A partial run preserves
all source states and last valid evidence. A fully unavailable collection is
persisted and exits nonzero. A retry with the same Job key uses the saved run
without HTTP. The application enforces its approved end date and stored-run
limit even if the CronJob remains present.

## Monitoring, pause and rollback

Check Job failure events and the JSON receipt after each scheduled window.
The initial slice has no notification integration; the operator must monitor
the two weekly windows until alert delivery is configured and tested. Do not
describe notifications as active.

To pause, change suspend to true in Git and sync with approval. Suspension
prevents new scheduled Jobs; it does not terminate a running Job. Cancelling
that Job is a separate reviewed operation. No database deletion is needed.

To roll back code, retain suspension, promote the previous verified image
digest through GitOps, run a report reconstruction and a controlled smoke.
The initial schema is additive and append-only; never automatically drop it
during app removal or rollback. Keep the owner Secret outside collector pods.

Restore data only to an isolated destination first. Validate row count and
payload hashes, then reconstruct the latest HTML. Production replacement,
credential rotation or destructive recovery requires a separate plan, current
backup and exact approval. Preserve the failed source for analysis.

## Verification and handoff

Offline verification covers rendering 13 resources, schedule, timeout,
concurrency, no API token, owner credential exclusion, the two-secret ESO read
scope, manual sync and digest enforcement for activation. Both Ansible
playbooks pass syntax check. This is not a server dry-run or drift assessment.

State alterado: proposed GitOps files and standalone provisioning playbooks only.
Fonte de verdade: rbx-infra for desired infrastructure; private app repository for collector and configuration.
Backend remoto: feature branch and PR; PostgreSQL Jaguar is proposed for runtime data.
Backup e última execução: production evidence unknown; isolated application dump tested separately.
Último restore testado: isolated PostgreSQL 16 application test; production restore blocked pending evidence.
RPO/RTO: proposed 24h/2h, operator acceptance pending.
IaC/commit: this feature branch; no apply or sync.
Drift encontrado: live drift not assessed.
Estado local residual: reproducible worktree and offline render only.
Riscos ou bloqueios: production recovery evidence, trusted operator access, TLS, private image publication/pull and exact operation approvals.

Text follows rbx-voice-system v0.1, R4/R7 with R6 overlay.
