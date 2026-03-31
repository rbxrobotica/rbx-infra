# DNS and Email Architecture Plan

> Status: **Approved** | Date: 2026-03-31 | Phase: Pre-implementation

## Overview

Authoritative DNS and institutional email infrastructure for RBX Systems.
Sovereign, Ansible-automated, separated from the k3s cluster.

---

## DNS Architecture

### Topology

```
                    ┌─────────────────┐
                    │   Registrars    │
                    │ (Glue Records)  │
                    └────────┬────────┘
                             │
              ┌──────────────┴──────────────┐
              ▼                              ▼
     ┌────────────────┐            ┌────────────────┐
     │    pantera      │            │     eagle       │
     │  149.102.139.33 │            │  167.86.92.97   │
     │  ns1.rbxsystems │            │  ns2.rbxsystems │
     │     .ch         │            │     .ch         │
     │                 │            │                 │
     │  PowerDNS Auth  │───AXFR───►│  PowerDNS Auth  │
     │  (primary)      │  NOTIFY   │  (secondary)    │
     │  gpgsql backend │            │  bind backend   │
     └───────┬─────────┘            └─────────────────┘
             │
             │ PostgreSQL (port 5432)
             ▼
     ┌────────────────┐
     │    jaguar       │
     │  161.97.147.76  │
     │  ParadeDB       │
     │  (db: pdns)     │
     └────────────────┘
```

### Nodes

| Node | IP | IPv6 | Role | Backend |
|------|----|------|------|---------|
| pantera | 149.102.139.33 | 2a02:c207:2256:6730::1 | ns1 — primary | gpgsql (Postgres on jaguar) |
| eagle | 167.86.92.97 | 2a02:c207:2252:7581::1 | ns2 — secondary | bind (zone files via AXFR) |
| jaguar | 161.97.147.76 | — | Postgres state backend | — |

### Key Decisions

- **Software:** PowerDNS Authoritative 4.9 — Postgres-native backend, REST API, minimal config
- **Secondary uses bind backend, NOT gpgsql** — reduces jaguar dependency, keeps secondary functional without DB
- **dnsdist deferred to phase 2** — unnecessary for 2 zones with minimal traffic
- **DNS servers are dedicated** — no k3s workloads, no other services
- **DNSSEC deferred to phase 2**

### Zones

- `rbxsystems.ch` — institutional
- `strategos.gr` — product (already delegated to ns1/ns2.rbxsystems.ch)

### Replication

- Primary (pantera) → NOTIFY → secondary (eagle)
- Eagle requests AXFR/IXFR from pantera
- ACL: only eagle IPs allowed for AXFR

### Delegation (registrar actions)

```
Registrar rbxsystems.ch → glue records:
  ns1.rbxsystems.ch  →  149.102.139.33 / 2a02:c207:2256:6730::1
  ns2.rbxsystems.ch  →  167.86.92.97   / 2a02:c207:2252:7581::1

Registrar strategos.gr → NS only (glue lives in .ch):
  ns1.rbxsystems.ch
  ns2.rbxsystems.ch
```

---

## Email Architecture

### Provider

Postmark — outbound only. No self-hosted MTA.

### Domain Structure

| Type | Domain | Purpose |
|------|--------|---------|
| Institutional | rbxsystems.ch | Company communication |
| Product | strategos.gr | Product communication |
| Transactional (institutional) | tx.rbxsystems.ch | System emails (no-reply, alerts) |
| Transactional (product) | tx.strategos.gr | Product system emails |

### Reputation Isolation

Each domain/subdomain has independent SPF/DKIM/DMARC. If `tx.strategos.gr` gets spam-flagged, `rbxsystems.ch` reputation is unaffected.

### Address Map

#### rbxsystems.ch

| Address | Type | Destination |
|---------|------|-------------|
| contact@ | Inbox real | Main company inbox |
| ceo@ | Inbox real | Leandro personal |
| finance@ | Alias | → ceo@ |
| billing@ | Alias | → finance@ |
| legal@ | Alias | → ceo@ |
| sales@ | Alias | → contact@ |
| partnerships@ | Alias | → ceo@ |
| support@ | Alias | → contact@ |

#### tx.rbxsystems.ch

| Address | Type | Destination |
|---------|------|-------------|
| no-reply@ | Sender only | No inbox. Outbound via Postmark |
| alerts@ | Sender only | No inbox. Outbound to internal team |

#### strategos.gr

| Address | Type | Destination |
|---------|------|-------------|
| support@ | Alias | → contact@rbxsystems.ch (phase 1) |

#### tx.strategos.gr

| Address | Type | Destination |
|---------|------|-------------|
| no-reply@ | Sender only | No inbox. Outbound via Postmark |

### Usage Policy

- Human → Human: root domain only (`@rbxsystems.ch`). Must accept replies.
- System → Human: `tx.*` subdomains only. From: `no-reply@tx.*`.
- Marketing: NEVER on `tx.*`. Future: dedicated `mkt.*` subdomain.
- Alerts: `alerts@tx.rbxsystems.ch` → internal only, subject prefix `[ALERT]`.
- Each new product gets its own `tx.{product}.{tld}` before sending any email.

### Postmark Servers

| Server Name | Domain | Use |
|-------------|--------|-----|
| RBX Institutional | rbxsystems.ch | contact@, ceo@ outbound |
| RBX Transactional | tx.rbxsystems.ch | no-reply@, alerts@ |
| Strategos Transactional | tx.strategos.gr | no-reply@ |

---

## DNS Records

### rbxsystems.ch

```dns
; NS
@               NS      ns1.rbxsystems.ch.
@               NS      ns2.rbxsystems.ch.
ns1             A       149.102.139.33
ns1             AAAA    2a02:c207:2256:6730::1
ns2             A       167.86.92.97
ns2             AAAA    2a02:c207:2252:7581::1

; Web
@               A       158.220.116.31
www             CNAME   rbxsystems.ch.

; Email
@               MX      10  inbound.postmarkapp.com.
@               TXT     "v=spf1 include:spf.mtasv.net ~all"
_dmarc          TXT     "v=DMARC1; p=none; rua=mailto:dmarc@rbxsystems.ch; fo=1"
; pm._domainkey CNAME   (provided by Postmark after setup)

; Transactional subdomain
tx              A       158.220.116.31
tx              MX      10  inbound.postmarkapp.com.
tx              TXT     "v=spf1 include:spf.mtasv.net ~all"
_dmarc.tx       TXT     "v=DMARC1; p=none; rua=mailto:dmarc@rbxsystems.ch; fo=1"
```

### strategos.gr

```dns
; NS
@               NS      ns1.rbxsystems.ch.
@               NS      ns2.rbxsystems.ch.

; Web
@               A       158.220.116.31
www             CNAME   strategos.gr.

; Email
@               MX      10  inbound.postmarkapp.com.
@               TXT     "v=spf1 include:spf.mtasv.net ~all"
_dmarc          TXT     "v=DMARC1; p=none; rua=mailto:dmarc@rbxsystems.ch; fo=1"

; Transactional subdomain
tx              A       158.220.116.31
tx              MX      10  inbound.postmarkapp.com.
tx              TXT     "v=spf1 include:spf.mtasv.net ~all"
_dmarc.tx       TXT     "v=DMARC1; p=none; rua=mailto:dmarc@rbxsystems.ch; fo=1"
```

### Progressive Hardening

| Phase | SPF | DMARC |
|-------|-----|-------|
| 1 (now) | `~all` (softfail) | `p=none` (monitor) |
| 2 | `-all` (hardfail) | `p=quarantine` |
| 3 | `-all` | `p=reject` |

---

## Ansible Structure

```
bootstrap/ansible/
├── inventory/hosts.yml          # UPDATE: pantera/eagle → dns_servers group
├── group_vars/
│   ├── all.yml
│   ├── dns_servers.yml          # NEW: PowerDNS vars
│   └── vault.yml                # pdns_db_password, pdns_api_key
├── host_vars/
│   ├── pantera.yml              # pdns_role: primary, backend: gpgsql
│   └── eagle.yml                # pdns_role: secondary, backend: bind
├── roles/
│   ├── hardening/               # EXISTING — reuse for DNS servers
│   ├── pdns-database/           # NEW — creates pdns db/user on jaguar
│   ├── pdns/                    # NEW — installs and configures PowerDNS
│   │   ├── tasks/main.yml
│   │   ├── handlers/main.yml
│   │   ├── templates/
│   │   │   ├── pdns-primary.conf.j2
│   │   │   └── pdns-secondary.conf.j2
│   │   └── defaults/main.yml
│   └── dns-hardening/           # NEW — DNS-specific firewall (port 53 open, no 80/443)
└── site.yml                     # UPDATE: add phases 5-7 for DNS
```

### Inventory Changes

pantera and eagle move from `k3s_agents` to `dns_servers` group. Two new 8GB VPSs join `k3s_agents`.

---

## Implementation Order

### Pre-requisites (cluster migration)

1. Contract 2 new 8GB VPSs
2. Provision as k3s agents (Ansible k3s-agent role)
3. `kubectl drain pantera --ignore-daemonsets --delete-emptydir-data`
4. `kubectl drain eagle --ignore-daemonsets --delete-emptydir-data`
5. `kubectl delete node pantera && kubectl delete node eagle`
6. Verify cluster healthy with new nodes
7. Reinstall pantera + eagle (clean Ubuntu 24.04)

### DNS deployment

8. Run Ansible: pdns-database role on jaguar
9. Run Ansible: hardening + dns-hardening on pantera/eagle
10. Run Ansible: pdns role on pantera (primary), then eagle (secondary)
11. Seed zone records (pdnsutil or SQL)
12. Validate: `dig @149.102.139.33 rbxsystems.ch SOA`
13. Validate: `dig @167.86.92.97 rbxsystems.ch SOA` (AXFR worked)
14. Update glue records at .ch registrar
15. Update NS at .gr registrar
16. Validate: `dig rbxsystems.ch NS +trace`

### Email deployment

17. Create Postmark servers (3 servers)
18. Add DKIM CNAME records to zones
19. Verify domains in Postmark dashboard
20. Test send from each server
21. Validate: `dig pm._domainkey.rbxsystems.ch CNAME`

---

## Hardening (DNS servers)

### Firewall (pantera/eagle)

| Port | Proto | Source | Purpose |
|------|-------|--------|---------|
| 22 | TCP | Any (fail2ban) | SSH |
| 53 | UDP | Any | DNS queries |
| 53 | TCP | Any | DNS transfers / TCP fallback |
| Default | * | * | **DENY** |

No port 80/443 — these are not web servers.

### Firewall (jaguar — additional)

| Port | Proto | Source | Purpose |
|------|-------|--------|---------|
| 5432 | TCP | 149.102.139.33, 167.86.92.97 | PowerDNS → Postgres |

### Backup

Daily `pg_dump` of `pdns` database on jaguar. 30-day retention.

### Monitoring

- `dig @pantera` and `dig @eagle` SOA serial comparison
- `dig +trace` from external
- Postmark dashboard for bounce/delivery rates
- DMARC aggregate reports to `dmarc@rbxsystems.ch`

---

## Phases Summary

| Phase | Scope |
|-------|-------|
| **1 (now)** | PowerDNS primary/secondary, Postmark, Ansible roles, SPF ~all, DMARC p=none |
| **2** | dnsdist (rate-limiting), DNSSEC, 3rd NS (different region), Prometheus exporters, SPF -all, DMARC p=quarantine |
| **3** | DMARC p=reject, automated report parsing, possible own mail server |
