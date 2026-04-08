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

All application manifests live in `apps/prod/` and `apps/staging/`.
ArgoCD syncs from this repository to the k3s cluster.
No direct `kubectl apply` in production.

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

## IaC layer summary

See `docs/infra/IAC-STRATEGY.md` for the full Ansible/Terraform boundary decision.

| Layer | Tool | Location |
|-------|------|----------|
| Host config, k3s, PowerDNS install | Ansible | `bootstrap/ansible/` |
| DNS zones and records | Terraform | `infra/terraform/dns/` |
| Application manifests | GitOps (ArgoCD) | `apps/` |
| Platform services | GitOps (ArgoCD) | `platform/` |
