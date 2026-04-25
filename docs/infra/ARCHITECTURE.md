# RBX Infrastructure Architecture

**Date:** 2026-04-07
**Status:** Active

## Node Map

| Node | IP | RAM | Role | Group |
|------|----|-----|------|-------|
| tiger | 158.220.116.31 | — | k3s control-plane | k3s_server |
| jaguar | 161.97.147.76 | — | k3s agent, ParadeDB host | k3s_agents, db_server |
| altaica | 173.212.246.8 | 8GB | k3s agent | k3s_agents |
| sumatrae | 5.189.178.212 | 8GB | k3s agent | k3s_agents |
| pantera | 149.102.139.33 | 4GB | ns1.rbxsystems.ch — DNS primary | dns_servers |
| eagle | 167.86.92.97 | 4GB | ns2.rbxsystems.ch — DNS secondary | dns_servers |

**Decommissioned:** bengal (164.68.96.68) — removed 2026-04-07.
**Reinstalling:** pantera + eagle — VPS images being reinstalled 2026-04-07, will rejoin as DNS-only nodes.

## Three planes

### Compute plane (k3s)

```
tiger (control-plane)
├── jaguar   (analytics workloads, ParadeDB)
├── altaica  (general workloads, 8GB)
└── sumatrae (general workloads, 8GB)
```

Managed by: Ansible (`bootstrap/ansible/`) + ArgoCD (application manifests).
State stored in: k3s etcd on tiger.

### DNS plane (PowerDNS)

```
pantera — ns1.rbxsystems.ch
  primary, gpgsql backend → PostgreSQL on jaguar (pdns db)
  notifies eagle on zone changes

eagle — ns2.rbxsystems.ch
  secondary, bind backend (no DB dependency)
  receives AXFR/IXFR from pantera
```

Service installed by: Ansible (`roles/pdns`, `roles/pdns-database`).
Zone records managed by: Terraform (`infra/terraform/dns/`).
Zones served: `rbxsystems.ch`, `strategos.gr`.

### Application plane (ArgoCD + GitOps)

All application manifests live in `apps/prod/`, `apps/staging/`, and `apps/testnet/`.
ArgoCD syncs from this repository to the k3s cluster.
No direct `kubectl apply` in production.

## Environment model

rbx-infra recognizes three environment tiers:

| Tier | Path | Namespace convention | Purpose |
|------|------|---------------------|---------|
| Production | `apps/prod/{app}/` | `{app}` | Live workloads |
| Testnet | `apps/testnet/{app}/` | `{app}-testnet` | Exchange-connected validation with synthetic capital |
| Staging | `apps/staging/` | `staging` | Shared pre-production for non-exchange services |

Environments are **birth-time properties**, not runtime flags. A deployment artifact is testnet or production from the moment it is created in rbx-infra — not because of a flag toggled at runtime.

### Robson v3 testnet environment

The first and only active testnet environment is `robson-testnet`. It was established in April 2026 to validate Robson v3 against the Binance testnet exchange before committing real capital.

Key properties:
- Namespace: `robson-testnet`
- Binance endpoint: `testnet.binance.vision` (enforced by `ROBSON_BINANCE_USE_TESTNET: "true"` in the ConfigMap)
- Database: `robson_testnet` on the existing ParadeDB instance (separate logical database)
- Projection stream key: `robson:testnet` (isolated from the production event stream)
- ArgoCD Application: `robson-testnet` (separate from `robson-prod`, separate destination namespace)

This is a concrete instance of namespace isolation, not a generic multi-environment framework. See `docs/ROBSON-TESTNET-ENVIRONMENT.md` for the full specification and `docs/adr/ADR-0003-robson-testnet-isolation.md` for the architectural decision.

### Environment isolation rules

1. An ArgoCD Application targeting `apps/testnet/{app}/` must have `destination.namespace: {app}-testnet`. Never `{app}`.
2. A Secret in `{app}-testnet` namespace must be bootstrapped from `rbx/{app}-testnet/` pass paths. Never from `rbx/{app}/`.
3. `ROBSON_BINANCE_USE_TESTNET` may only appear in `apps/testnet/robson/`. Its presence in `apps/prod/robson/` is an incident.
4. The testnet and production ArgoCD Applications never share source paths.

## Domain portfolio

| Domain | Purpose | NS |
|--------|---------|-----|
| rbxsystems.ch | Institutional | ns1/ns2.rbxsystems.ch |
| strategos.gr | Product | ns1/ns2.rbxsystems.ch |
| rbx.ia.br | Brazilian presence | External (to be migrated) |

## Postmark (email)

Outbound only. No self-hosted MTA.

| Postmark server | Domain | Senders |
|----------------|--------|---------|
| RBX Institutional | rbxsystems.ch | contact@, ceo@ |
| RBX Transactional | tx.rbxsystems.ch | no-reply@, alerts@ |
| Strategos Transactional | tx.strategos.gr | no-reply@ |

SMTP credentials stored as Kubernetes Secret in the cluster.
DNS records (SPF, DKIM, DMARC) managed via Terraform.

## Secrets plane

`pass` (GPG-encrypted git repo) is the single source of truth for all secrets.

```
pass (operator's machine)
  │
  ├─ bootstrap/scripts/init-vault-from-pass.sh
  │     → writes vault.yml (gitignored)
  │     → used by Ansible for DB provisioning on VPS hosts
  │
  └─ Ansible k8s-secrets role (Phase 8)
        → creates Kubernetes Secret objects in the cluster
        → robsond-secret, ghcr-pull-secret, ...
```

Nothing sensitive is committed to git. ArgoCD manages only non-sensitive manifests.
See `docs/infra/SECRETS.md` for the full model, pass namespace structure, and Day-0 setup.

## IaC layer summary

See `docs/infra/IAC-STRATEGY.md` for the full Ansible/Terraform boundary decision.
See `docs/infra/SECRETS.md` for the secrets model.

| Layer | Tool | Location |
|-------|------|----------|
| Host config, k3s, PowerDNS install | Ansible | `bootstrap/ansible/` |
| DNS zones and records | Terraform | `infra/terraform/dns/` |
| Application manifests | GitOps (ArgoCD) | `apps/` |
| Platform services | GitOps (ArgoCD) | `platform/` |

---

## Cluster access

The operator-issued kubeconfig lives at `~/.kube/config-rbx` on
the workstation. All `kubectl` commands in this repo and its
runbooks assume:

```bash
export KUBECONFIG=~/.kube/config-rbx
```

Do not commit kubeconfigs to git. New engineers receive a
service-account kubeconfig from the operator on first day; see
`docs/onboarding/ENGINEER-DAY-ONE.md`.

---

## Cluster baseline services

These services run on every cluster regardless of tier:

| Service | Namespace | Purpose | ClusterIssuer / IngressClass |
|---------|-----------|---------|------------------------------|
| ArgoCD | `argocd` | GitOps controller | n/a |
| cert-manager | `cert-manager` | TLS issuance via Let's Encrypt | `letsencrypt-prod` |
| Traefik | `kube-system` | Ingress controller | `traefik` |
| external-secrets | `external-secrets-system` | Secret sync from external stores | n/a |
| monitoring | `monitoring` | Prometheus/Grafana stack | n/a |

Application Ingresses target `ingressClassName: traefik` and
annotate `cert-manager.io/cluster-issuer: letsencrypt-prod` to
get TLS automatically. See
`docs/runbooks/CERT-MANAGER-DEBUG.md` for failure modes.

**Gateway API note.** Some legacy manifests reference `HTTPRoute`
(Gateway API) targeting a `robson-gateway` resource. That Gateway
is not currently provisioned in production; routing in the
cluster is Ingress-only today. The HTTPRoute migration is a
follow-up, not a current state. Do not write new HTTPRoutes
until the Gateway is provisioned and documented.

---

## Environment tiers

| Tier | Path | Namespace pattern | Notes |
|------|------|-------------------|-------|
| Production | `apps/prod/<app>/` | `<app>` | Live workloads |
| Testnet | `apps/testnet/<app>/` | `<app>-testnet` | Exchange-connected, synthetic capital |
| Staging | `apps/staging/<app>/` | `staging` | Shared, non-exchange |
| Dev sandbox | `apps/dev-sandboxes/<app>/` | `dev-<app>-<user>-<rand>` | Per-engineer ephemeral environments |

Dev sandboxes are intentionally short-lived. They are the only
place in the cluster where Postgres may run in a `StatefulSet`;
see "Database constraint" below.

---

## Database constraint (non-negotiable)

**PostgreSQL never runs inside the production k3s cluster.**
ParadeDB, the PowerDNS backend, and any application database are
hosted on dedicated VPS instances managed by Ansible
(`bootstrap/ansible/`). The cluster is treated as fully ephemeral
compute.

The only exceptions:

- Per-test ephemeral databases created by `sqlx::test` in Robson
  CI runs (lifecycle measured in seconds).
- Per-engineer dev sandboxes under `apps/dev-sandboxes/`
  (lifecycle measured in days).

This rule is operator policy, confirmed 2026-04-25 during the
PDNS incident debugging. Production stateful data must survive
cluster rebuild. See `docs/infra/DATABASE.md` for the canonical
external-database pattern.
