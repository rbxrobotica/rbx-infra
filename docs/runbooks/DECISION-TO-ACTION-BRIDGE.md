# Decision-to-Action Bridge: FlightDeck → Public Presence → Maestro → Corbetti

**Owner:** rbx-infra (GitOps) with rbx-flightdeck, rbx-public-presence, rbx-maestro.
**Governance:** strategos-core ADR-0010 §11 (command by reference), rbx-governance ADR-0015
and ADR-0500, rbx-flightdeck ADR 0010, rbx-maestro ADR-0006.

## What this chain is

Strategos decides direction and never admits work. FlightDeck plans the week and holds
the limited authorization. Public Presence materializes content and creative jobs.
Maestro admits missions. Corbetti executes them repository-bound and opens PRs.
Outcomes flow back as evidence.

```
FlightDeck  --(source event, HTTPS, PUBLIC_PRESENCE_SERVICE_KEY)-->  Public Presence API
FlightDeck  --(V2 admission, HTTPS, MAESTRO_ADMIT_KEY)------------->  Maestro (dispatch_blocked=true)
CronJob     --(POST, PUBLIC_PRESENCE_WORKER_KEY)------------------->  FlightDeck /internal/workers/public-presence
Corbetti    --(GET /leases/next every 30 s, runner key)------------>  Maestro
```

## State found on 2026-09-23 (why this runbook exists)

| Link | State | Fix in this change set |
|---|---|---|
| rbx-public-presence in prod | Namespace empty; ArgoCD stuck on the db-migrate hook because the image tag was a placeholder and `ghcr-pull-secret` was missing | Real image pin; Ansible task for the pull secret |
| Public Presence API reachability | Cluster-internal only; FlightDeck rejects non-HTTPS URLs | `/api/v1` path rule on `presence.rbx.ia.br` to the API Service |
| FlightDeck worker endpoint | No caller anywhere | CronJob every 5 minutes |
| FlightDeck → Public Presence / Maestro config | Not configured in prod (5 env vars absent) | Env and ExternalSecret keys |
| Maestro Flight Deck ingress | Disabled in prod (`AGENT_LOOP_FLIGHTDECK_KEY` absent) | Env and ExternalSecret; allowlist `rbxrobotica/rbx-creatives`; `MAESTRO_ENVIRONMENT=production` |
| Corbetti runner | Alive, polling, always 204 (no work) | Nothing to do; work will appear once admission runs |
| Activation | Not wired anywhere | Deliberately left disabled; request-bound dispatch approval is the next slice (see below) |

## State observed on 2026-09-24 02:10 UTC, right after the merges

PRs rbx-infra#268 and #269, rbx-maestro#23 and #24, rbx-public-presence#6 and
rbx-flightdeck#15 were merged without owner steps 1 to 5 below having been executed
first. ArgoCD auto-synced and the cluster is in the failure mode this runbook warned
about (read-only observation, nothing was changed by hand):

| Application | ArgoCD | Observed cause |
|---|---|---|
| `rbx-flightdeck` | OutOfSync, Degraded, sync retrying | New ReplicaSet cannot start; the worker CronJob pod fails with `couldn't find key PUBLIC_PRESENCE_WORKER_KEY in Secret rbx-flightdeck/rbx-flightdeck-secrets` (step 2 missing). The old pods (`sha-f9a1c63`) keep serving. |
| `rbx-maestro` | Synced, Degraded | New pod (`sha-cf3d94b`) fails with `secret "maestro-flightdeck-key" not found`; the ExternalSecret reports `UpdateFailed` (step 3 missing). Maestro is down until the key exists or the rollout is reverted. |
| `rbx-public-presence` | OutOfSync, waiting for the migrate hook | The hook job still pulls the placeholder `sha-000…` image and `ghcr-pull-secret` is missing (step 4). The kustomization pin is `9ba13e2`; the merged PR #6 (`31ade92`) has not been promoted (step 5). |

Recovery is the owner steps below, in order. Two additions since the first version of
this runbook, brought by the activation slice (rbx-flightdeck#15, rbx-maestro#24):

- `rbx-flightdeck-secrets` also needs `MAESTRO_DISPATCH_KEY`, distinct from
  `MAESTRO_ADMIT_KEY`; the worker fails closed if any two of `MAESTRO_ADMIT_KEY`,
  `MAESTRO_DISPATCH_KEY`, `PUBLIC_PRESENCE_WORKER_KEY`, `PUBLIC_PRESENCE_SERVICE_KEY`
  are equal. Mint it with `openssl rand -hex 32 | pass insert -e rbx/maestro/flightdeck-dispatch-key`.
- Maestro activation stays blocked until `AGENT_LOOP_FLIGHTDECK_DISPATCH_KEY` is
  provisioned with that same value (rbx-maestro ADR-0006: provisioning a key never
  authorizes activation by itself; the governance decision is rbx-governance ADR-0614).
- After steps 4 and 5, bump `apps/prod/rbx-public-presence` to the image of merge
  commit `31ade92` (rbx-public-presence#6) through a normal PR; do not hand-edit tags in
  the cluster.

Once the pods are green, the first end-to-end proof in production is the four-repository
E2E's path with a real standing authorization: expect `{"stage":"idle"}` from the worker
until an Action is approved and a standing authorization exists, then policy dispatch,
admission, materialization, activation, Corbetti delivery, import and a SCHEDULED
publication whose outbox worker is still off until the owner enables it.

## What this change set does NOT do

- It does not provision `AGENT_LOOP_FLIGHTDECK_DISPATCH_KEY`. Admission stores every
  Flight Deck mission as `dispatch_blocked=true`; nothing is activated and the runner
  keeps receiving 204. rbx-maestro ADR-0006 is explicit that setting keys does not
  authorize production activation.
- Activation is **request-bound**, not a Mandato matter. Per the Flight Deck contract
  (`rbx-flightdeck` `docs/provider-neutral-execution.md`, Autonomy): an approved Action
  plus a current standing authorization plus a separate dispatch approval for the exact
  request lets later stages run unattended; Maestro activation resolves that
  request-bound dispatch approval, and Action approval alone never clears the dispatch
  block. Strategos and its Mandato only bound the standing authorization; they never
  activate a Mission. The activation stage (dispatch key, policy-produced dispatch
  decision, creative job materialization, result reconciliation, publication) was merged
  on 2026-09-24 as rbx-flightdeck#15, rbx-public-presence#6, rbx-maestro#24 and
  rbx-infra#269; it runs only with the keys above provisioned.
- It does not publish anything externally.
- It does not create any secret value in Git.

## Owner steps (in order, all outside Git)

Steps 1 to 5 were meant to happen **before** merging this change set (they did not; see the state observed on 2026-09-24 above, so they are now the recovery path). ArgoCD auto-syncs on merge;
if the secrets or the pull secret are missing at that moment, FlightDeck and Maestro
pods fail to start (missing `secretKeyRef` keys) and Public Presence stays blocked on
the migrate hook.

1. **Mint the two new keys and the Maestro copy** (hex only, per SECRETS.md):
   ```bash
   openssl rand -hex 32 | pass insert -e rbx/flightdeck/public-presence-worker-key
   openssl rand -hex 32 | pass insert -e rbx/maestro/flightdeck-key
   ```
2. **Extend `rbx-ia-br/rbx-flightdeck-secrets`** with the three new keys. The service key
   is the existing Public Presence one; the admit key is the value minted above:
   ```bash
   kubectl -n rbx-ia-br patch secret rbx-flightdeck-secrets --type=merge -p "$(python3 - <<'PY'
   import base64, json, subprocess
   def b(p): return base64.b64encode(subprocess.check_output(['pass','show',p]).strip()).decode()
   print(json.dumps({"data": {
     "PUBLIC_PRESENCE_WORKER_KEY": b('rbx/flightdeck/public-presence-worker-key'),
     "PUBLIC_PRESENCE_SERVICE_KEY": b('rbx/public-presence/service-key'),
     "MAESTRO_ADMIT_KEY": b('rbx/maestro/flightdeck-key')}}))
   PY
   )"
   ```
3. **Create `rbx-ia-br/maestro-flightdeck-key`** (same value as `MAESTRO_ADMIT_KEY`):
   ```bash
   kubectl -n rbx-ia-br create secret generic maestro-flightdeck-key \
     --from-literal=key="$(pass show rbx/maestro/flightdeck-key)"
   ```
4. **Run the k8s-secrets role** so `ghcr-pull-secret` lands in `rbx-public-presence`
   (or, as a one-off, copy the Ansible task's `kubectl create secret docker-registry`
   command by hand; the role is canonical).
5. **Add `rbx-public-presence` to the `RBX_INFRA_PAT` org-secret repository visibility**
   (GitHub org settings). Until then every merge to that repo builds images but fails the
   promote step with `Input required and not supplied: token`, and tags must be bumped by
   hand as this change set did.
6. Merge this change set. ArgoCD converges: Public Presence syncs (migration hook, api,
   web), FlightDeck and Maestro roll to pick up the new env.

## Verification

```bash
export KUBECONFIG=~/.kube/config-rbx
kubectl get application -n argocd rbx-public-presence rbx-flightdeck rbx-maestro
kubectl get pods -n rbx-public-presence
kubectl get externalsecret -n rbx-flightdeck
kubectl get externalsecret -n rbx-maestro
kubectl get cronjob -n rbx-flightdeck rbx-flightdeck-public-presence-worker
# first worker runs: expect {"stage":"idle"} until an Action is approved in FlightDeck
kubectl logs -n rbx-flightdeck job/$(kubectl get job -n rbx-flightdeck -o name | grep public-presence-worker | tail -1 | cut -d/ -f2)
# Maestro side: admissions appear as POST .../flightdeck/v2/execution-requests:admit 200
kubectl logs -n rbx-maestro deploy/rbx-maestro --since=1h | grep 'execution-requests:admit'
```

A worker response of `503 Public Presence worker is disabled` means one of the three
FlightDeck env vars is still missing. `401 Unauthorized` means the CronJob key and the
FlightDeck key differ. Maestro `E-INVALID-FLIGHTDECK-V2: target_environment is not
enabled` means `MAESTRO_ENVIRONMENT` and `MAESTRO_TARGET_ENVIRONMENT` differ.

## Image provenance note

`TestVerifyPassClaimsAcrossReplicas` in rbx-public-presence `internal/publishing` exposed
a real race between replicas (reproduced 7 of 10 runs), fixed in commit `e6ca605`
("reject stale verification claims"). The pin in this change set is `9ba13e2`, the merge
commit that contains that fix. Do not pin `38d9c91` or older.
