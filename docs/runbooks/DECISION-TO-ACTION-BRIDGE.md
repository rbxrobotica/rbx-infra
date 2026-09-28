# Decision-to-Action Bridge: Flight Deck, Public Presence, Maestro and Corbetti

**Owner:** rbx-infra (GitOps), with rbx-flightdeck, rbx-public-presence and
rbx-maestro.

**Governance:** rbx-governance ADR-0015 and ADR-0614, rbx-flightdeck ADR 0010,
rbx-maestro ADR-0006 and ADR-0007. strategos-core ADR-0010 section 11 is Draft
context, not ratified authority.

## Responsibility chain

Strategos decides direction and never admits work. Flight Deck plans the week,
records the Action and holds the bounded standing authorization. Public Presence
materializes content and creative jobs, imports outputs, applies owner-side
policy and owns publication truth. Maestro admits and activates immutable
Missions. Corbetti executes repository work and opens pull requests. Each owner
keeps its own evidence.

```text
Flight Deck  -- source event and materialization --> Public Presence
Flight Deck  -- V2 admission and activation ------> Maestro
Corbetti     -- claim, heartbeat and result ------> Maestro
Flight Deck  -- result projection and reconcile --> Maestro
Flight Deck  -- delivery or failure --------------> Public Presence
Public Presence -- outbox and receipt ------------> channel adapter
```

The repository profile name is routing, not provenance. Capability evidence
comes from the Corbetti probe and terminal manifest. `SCHEDULED` is a Public
Presence plan, not evidence that a channel accepted or displayed anything.

## Repository state after the 2026-09-23 merges

The implementation repositories now contain the complete request-bound slice:

1. Flight Deck writes an immutable intent, admits the blocked request, records a
   policy decision for an exact fingerprint inside a live authorization,
   materializes the item and job, activates and reconciles.
2. Maestro admits and activates with separate credentials, leases atomically,
   validates capability evidence, projects results without lease credentials and
   reconciles silent or expired work without inventing runner evidence.
3. Corbetti probes capabilities before repository mutation, runs the admitted
   executor in an isolated worktree, applies path and verify policy, opens a pull
   request and submits one terminal manifest.
4. Public Presence imports the committed delivery from the repository owner
   side, verifies hashes and format constraints, settles failure or expiry, and
   may approve and plan a publication under the projected authorization.
5. `e2e/decision-to-action/run.sh` proves the four-repository
   `claude_code_design` degraded-bundle path through `SCHEDULED`. The harness
   deliberately runs no live channel adapter.

This is code and contract evidence. It is not proof that production has been
activated.

## Current production posture

The GitOps state on `main` remains admission-only by construction:

| Surface | Current Git state | Operational consequence |
| --- | --- | --- |
| Flight Deck | image `sha-15223abe...`, worker CronJob present | activation code exists |
| Maestro | image `sha-93bf248f...` | result projection and reconciliation exist |
| Public Presence | image `sha-9ba13e29...`, before PR 6 | owner-side import and autonomous continuation from PR 6 are not in the pinned image |
| Dispatch credential | `AGENT_LOOP_FLIGHTDECK_DISPATCH_KEY` and `MAESTRO_DISPATCH_KEY` are absent from manifests | activation fails closed and Corbetti continues to receive no newly activated Flight Deck work |
| Corbetti capability slice | Ansible role code and contract tests are merged | host application of the role is a separate, owner-gated operation |
| Channel publication | existing outbox and adapters remain owner controlled | no remote effect is proven by the repository E2E |

Do not describe this state as live autonomous publication. Merging application
code did not create credentials, apply the Corbetti role, change the Public
Presence image pin or execute an ArgoCD sync.

## Admission prerequisites

Before changing activation state, verify rather than assume that the admission
prerequisites from PR 268 were completed:

- Public Presence is healthy and reachable at its HTTPS `/api/v1` ingress;
- the Flight Deck CronJob can call its purpose-specific worker route;
- Flight Deck has its Public Presence service key and Maestro admission key;
- Maestro has the matching Flight Deck admission key and the repository
  allowlist;
- the Public Presence namespace has the GHCR pull secret;
- the image-promotion token can update the rbx-infra pin for
  rbx-public-presence.

Read-only verification:

```bash
kubectl --kubeconfig <operator-kubeconfig> get application -n argocd rbx-public-presence rbx-flightdeck rbx-maestro
kubectl --kubeconfig <operator-kubeconfig> get pods -n rbx-public-presence
kubectl --kubeconfig <operator-kubeconfig> get externalsecret -n rbx-flightdeck
kubectl --kubeconfig <operator-kubeconfig> get externalsecret -n rbx-maestro
kubectl --kubeconfig <operator-kubeconfig> get cronjob -n rbx-flightdeck rbx-flightdeck-public-presence-worker
```

These commands inspect state only. Any secret change, Ansible application,
manifest merge, ArgoCD sync or production canary needs its own current operator
authorization.

## Activation promotion order

Activation is one later, separately reviewed GitOps change. Its safe order is:

1. Build and verify the rbx-public-presence image containing PR 6, then prepare
   its immutable image pin.
2. Prepare one distinct dispatch credential and project it to Maestro as
   `AGENT_LOOP_FLIGHTDECK_DISPATCH_KEY` and to Flight Deck as
   `MAESTRO_DISPATCH_KEY`. Do not reuse the admission credential.
3. Apply the current Corbetti agent-workbench role and verify the capability
   report contract on the host.
4. Keep the first production authorization narrow: one identity, profile,
   format, repository and source commit, one intent, short validity and no paid
   budget increase.
5. Rehearse through repository delivery and `SCHEDULED` with the channel remote
   effect disabled or directed to an explicitly admitted non-live target.
6. Reconcile every Mission, creative job and publication row and verify that
   the repository PR, imported hashes, QA record and authorization fingerprint
   agree.
7. Admit the live channel effect only as a separate operator decision after the
   rehearsal evidence is accepted. Completion is the owner-side verified or
   attested receipt, never the runner result or `SCHEDULED` state.

## Stop conditions

Stop activation and preserve evidence when any of these occurs:

- the standing authorization is missing, expired, revoked or exhausted;
- the request fingerprint or source commit differs at any layer;
- a required capability is unavailable and no named degraded mode was admitted;
- a provider name is being used as proof of a capability;
- imported bytes, hashes, MIME, dimensions, QA or pull request binding disagree;
- the result is terminal without the corresponding owner evidence;
- the channel outcome is unknown, a credential challenge appears, or the
  sender identity does not match the authorization.

An unknown remote effect is reconciled before any retry.

## Historical image provenance

The `sha-9ba13e29...` Public Presence pin includes the fix for the real
`TestVerifyPassClaimsAcrossReplicas` race from commit `e6ca605`. It does not
include PR 6. The next pin must include both that fix and the owner-side import
and autonomy changes; never regress to `38d9c91` or older.
