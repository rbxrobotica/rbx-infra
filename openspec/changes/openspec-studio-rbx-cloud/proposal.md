# Proposal: Host OpenSpec Studio in the RBX cloud and expose its entry through Atlas

Status: development backlog; implementation and deployment have not started.
Created: 2026-11-07 (America/Sao_Paulo).
Delivery owner: RBX Infra. Assignee, priority and target date are maintained in the linked GitHub issue.

GitHub delivery tracking: [issue #386](https://github.com/rbxrobotica/rbx-infra/issues/386).
This change versions the technical scope and checklist. Automatic GitHub progress publication is not configured yet.

## Why

OpenSpec Studio is currently a local Mint web application that projects each
repository's OpenSpec changes and tasks into a Kanban. RBX needs a cloud-hosted
entry point so authorized users can access development planning independently
of this workstation. The operator requested a cloud delivery and frontend access
through RBX Portal or RBX Atlas.

The current installation is loopback-only and trusts local processes. Its
Host/Origin protections are not multi-user authentication or a process sandbox.
Cloud delivery requires a reviewed access and workspace boundary.

## What Changes

- Package a pinned Studio release and required RBX patches in an explicitly
  owned application build; keep deployment configuration in rbx-infra.
- Prepare a private RBX cloud deployment with authenticated access, authorized
  repository/workspace access, persistent data and auditable operations.
- Prefer an internal Atlas entry that opens Studio's own authenticated frontend.
  Atlas remains navigation: no iframe embedding or reverse proxy. Portal's
  client-project readiness boundary remains unchanged; a client-facing projection
  would require its own audience and scope decision.
- Preserve repository OpenSpec files as the technical source. Define how cloud
  workspaces read and propose edits through branches/PRs, handle concurrent edits
  and recover uncommitted work; do not silently copy workstation state or create
  a second authoritative task database.

## Capabilities

### New Capabilities

- `openspec-studio-cloud-access`: authenticated cloud access to isolated,
  persistent planning workspaces, discovered through RBX Atlas.

### Modified Capabilities

None in the OpenSpec baseline. Detailed design, application ownership and delta
specs remain pending. This card does not activate a runtime or integration.

## Acceptance Criteria

1. Record application/build owner, target environment, exposure, hostname,
   capacity/cost envelope and Atlas integration. Separate app code/build from
   GitOps manifests. Do not claim a chosen domain or deployed service before
   the corresponding decision and evidence exist.
2. Integrate RBX Identity with server-side authorization per workspace/repository,
   authenticated user audit, session controls and the cloud Host/Origin/CSRF
   contract. Unauthenticated, cross-workspace and ungranted access are rejected.
3. Use allowlisted repositories and scoped credentials; confine paths and
   processes. Do not mount the workstation home, host runtime socket or broad
   cluster credentials. AI providers, arbitrary command execution and Specflow
   remain disabled until separately configured and authorized.
4. Define Git read/edit/commit/push, review, conflict and concurrency rules;
   preserve edits and provenance without silent overwrite or automatic main
   writes. Repeated operations and worker restarts must not duplicate changes.
5. Persist and inventory the Studio database, authorized workspaces and pending
   edits. Demonstrate backup/restore and version rollback without losing task
   state or uncommitted files. Specify database, Git/API, identity-provider and
   worker outages, visible degraded states, recovery and bounded retries.
6. Deliver an immutable, traceable image and reviewed environment manifests
   with external secret references, network/resource bounds, health probes and
   operational signals. Validate configuration before a separately authorized
   deployment; secrets never enter specifications, logs or repository files.
7. Demonstrate authorized login, denied access, workspace isolation, opening,
   creating and editing a card, restart persistence and conflict handling in an
   isolated acceptance environment. Record actual evidence independently from
   build/manifests and document rollout/rollback prerequisites.
8. Add a visibility-appropriate Atlas entry with owner, purpose, authenticated
   destination and evidence. Do not leak private hosts/metadata into the public
   Atlas build or bypass Studio authorization; respect the current Atlas
   exposure/authentication deployment state before publishing internal entries.

## Measurements

Define baseline and targets during design. Measure time to open an authorized
board, card load/save latency, resource consumption and monthly hosting estimate,
backup age/restore duration, workspace conflicts, failed writes and recovery.
Distinguish planned capacity/cost from observed usage and realized financial cost.

## Boundaries and Dependencies

- rbx-infra: environment configuration, GitOps, network/storage/secret references.
- Application owner to be selected: upstream pin, RBX patches, image build,
  authentication adapter and workspace isolation.
- rbx-atlas: internal discovery entry and its own exposure policy.
- RBX Identity: identity integration and scoped access contract.
- Source repositories: specification ownership, branch/PR review and permissions.

The design must compare independent Studio hosting plus Atlas navigation with
Portal integration, recording operational cost, audience, isolation and maintenance
trade-offs. Atlas navigation is the preferred direction based on current roles.
No new RBX application repository, public endpoint, provider credentials, production
deployment, DNS change or automatic agent execution is created by this card.

## References and Tracking

- [RBX Infra scope](../../../README.md) and [operation boundaries](../../../AGENTS.md).
- Existing local deployment evidence: workstation documentation
  `docs/workstation/OPENSPEC-STUDIO.md` and
  `docs/workstation/OPENSPEC-STUDIO-ACCEPTANCE-2026-10-20.md` in the Mint
  OpenSpec Studio worktree; source/build `d564f10f6e21-mint1`.
- Audience and navigation evidence: `rbx-atlas/README.md` and `rbx-portal/README.md`.

OpenSpec Studio displays this change as one card. Its technical checklist lives
in tasks.md; GitHub carries discussion, assignment and delivery references.
