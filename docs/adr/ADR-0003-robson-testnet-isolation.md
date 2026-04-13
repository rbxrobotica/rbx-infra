# ADR-0003: Robson v3 Testnet — Namespace Isolation in Single Cluster

## Status

**Accepted** — 2026-04-12

## Context

Robson v3 is a trading daemon executing against the Binance exchange. As of April 2026:

- Robson v3 runs in production namespace `robson` with Binance testnet credentials
- There is no formal testnet environment: one namespace, one set of secrets, no explicit environment boundary
- The Rust daemon (`robsond`) has a `BinanceRestClient::testnet()` constructor but no code path calls it conditionally based on configuration — the testnet endpoint is never actually used
- No protection prevents production Binance credentials from being injected into the same namespace
- Before live trading on Binance Spot (real capital), a validated, isolated testnet environment must exist as a distinct runtime artifact

Three options were evaluated:

| Option | Isolation level | Operational cost |
|--------|----------------|-----------------|
| Second Kubernetes cluster | Physical (network + host) | High — duplicates all platform services |
| Dedicated namespace in existing cluster | Logical (namespace scope) | Low — no new infrastructure |
| Reuse `staging` namespace | None (shared) | Zero — but unsafe for exchange workloads |

## Decision

Use a **dedicated namespace `robson-testnet`** in the existing k3s cluster.

The environment is defined by five independently isolated components:

1. **Namespace** `robson-testnet` — k8s scope boundary for secrets, RBAC, and quotas
2. **Secret** `robsond-testnet-secret` in `robson-testnet` — never contains production credentials
3. **Database** `robson_testnet` on the existing ParadeDB instance — separate logical database
4. **Projection stream key** `robson:testnet` — separate event stream, does not mix with prod
5. **ConfigMap key** `ROBSON_BINANCE_USE_TESTNET: "true"` — environment marker (see guardrails below)

## Rationale

### Why not a second cluster

No capital is at risk yet. Testnet does not require blast-radius isolation at the network level. The primary risk is credential confusion — namespace isolation addresses that completely.

A second cluster requires duplicating Ansible bootstrap, ArgoCD, cert-manager, ParadeDB, DNS, and TLS infrastructure. With a single-operator platform function, two clusters represent permanent operational debt without proportional benefit. If compliance or capital-risk requirements change, the namespace boundary is already the correct unit of migration to a second cluster.

### Why not reuse `staging`

`staging` is a shared multi-tenant namespace with multiple unrelated services. Robson testnet has a distinct operational profile: frequent restarts, high-volume trading logs, periodic exchange reconnects, and exchange-credential scope. Mixing this into `staging` creates quota pressure and log pollution that affects other services.

More importantly, the semantic is wrong. `staging` means "pre-production feature validation." `testnet` means "exchange behavior validation with synthetic capital." They have different operators, different lifecycle, and different risk profiles.

### Why namespace isolation is sufficient now

Kubernetes namespace scope guarantees Secret objects are never visible across namespaces — this is enforced by the API server, not convention. The ArgoCD Application destination is namespace-scoped: the `robson-testnet` ArgoCD app can only deploy to `robson-testnet`. The pass namespace `rbx/robson-testnet/` is never read by the production Ansible task.

The existing pattern (one namespace per product per environment) scales naturally. When and if a second cluster is justified, namespaces are already the correct unit of migration.

## Consequences

### Positive

- Zero new infrastructure cost — same cluster, same ArgoCD, same operational process
- Every resource in `robson-testnet` is testnet by construction — no human discipline required
- Reversible — removing the environment is `kubectl delete namespace robson-testnet`
- Coherent with the existing rbx-infra GitOps pattern

### Negative

- Logical isolation only — a misconfigured NetworkPolicy could theoretically allow cross-namespace access
- ParadeDB is a shared host — separate database, but same PostgreSQL instance on `jaguar`
- Does not satisfy hypothetical future compliance requirements for physical isolation

### When to revisit

Upgrade to a second cluster when any of these conditions hold:
- Real capital is at risk and a compliance audit requires physical isolation
- A second team has independent cluster access requirements
- Three or more products each have testnet environments running simultaneously
- A testnet incident caused collateral damage to the production cluster

## Guardrails established by this decision

### `ROBSON_BINANCE_USE_TESTNET` is an environment marker, not a runtime toggle

This variable controls which `BinanceRestClient` constructor is called in the Rust daemon. Its semantics are:

| Location | Allowed value | Meaning |
|----------|--------------|---------|
| `apps/testnet/robson/robsond-config.yml` | `"true"` | This deployment connects to `testnet.binance.vision` |
| `apps/prod/robson/` (any file) | **FORBIDDEN** | Incident — immediate review required |

**If `ROBSON_BINANCE_USE_TESTNET` appears in any file under `apps/prod/robson/`, that is an operational incident.** It must be removed before any subsequent deploy reaches the cluster. It must never be used as a temporary override or feature flag.

The variable is not a toggle between two live environments. It is a compile-time constant baked into the environment's ConfigMap that determines which exchange endpoint the daemon will ever connect to.

## Related

- `docs/ROBSON-TESTNET-ENVIRONMENT.md` — full implementation specification and operational guide
- `docs/infra/SECRETS.md` — pass namespace additions for `robson-testnet`
- `docs/infra/ARCHITECTURE.md` — environment model section
