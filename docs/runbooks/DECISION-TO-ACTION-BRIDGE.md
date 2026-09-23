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
| Activation | Not wired anywhere | Deliberately left disabled (see below) |

## What this change set does NOT do

- It does not provision `AGENT_LOOP_FLIGHTDECK_DISPATCH_KEY`. Admission stores every
  Flight Deck mission as `dispatch_blocked=true`; nothing is activated and the runner
  keeps receiving 204. rbx-maestro ADR-0006 is explicit that setting keys does not
  authorize production activation. Activation is the next decision (Strategos Mandato,
  ADR-0010 §5) and gets its own change.
- It does not publish anything externally.
- It does not create any secret value in Git.

## Owner steps (in order, all outside Git)

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

## Known flake

`TestVerifyPassClaimsAcrossReplicas` in rbx-public-presence `internal/publishing` failed
once on the merge commit 38d9c91 and passed on rerun without code change. Treat a red CI
on that test as a rerun candidate, and file the race as a follow-up in that repository.
