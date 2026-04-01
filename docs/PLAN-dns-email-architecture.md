# DNS and Email Architecture Plan

> Status: **Approved — Ready for rollout** | Date: 2026-04-01 | Phase: Pre-implementation

## Overview

Authoritative DNS and institutional email infrastructure for RBX Systems.
Sovereign, Ansible-automated, separated from the k3s cluster.

---

## Infrastructure Topology (updated 2026-04-01)

### Before

```
k3s cluster:
  server: tiger
  agents: eagle, pantera, bengal, jaguar

bengal: compromised, cordoned (2026-03-29)
```

### After

```
k3s cluster:
  server: tiger (158.220.116.31)
  agents: altaica (173.212.246.8), sumatrae (5.189.178.212), jaguar (161.97.147.76)

dns (standalone, outside k3s):
  primary: pantera (149.102.139.33)
  secondary: eagle (167.86.92.97)

decommissioned:
  bengal (164.68.96.68) — compromised, removed
```

### New VPSs

| Node | IPv4 | IPv6 | OS | Location | Role |
|------|------|------|----|----------|------|
| altaica | 173.212.246.8 | 2a02:c207:2319:6730::1 | Ubuntu 24.04 | Europe | k3s agent |
| sumatrae | 5.189.178.212 | 2a02:c207:2319:6729::1 | Ubuntu 24.04 | Europe | k3s agent |

---

## DNS Architecture

### Topology

```
                    +-------------------+
                    |    Registrars     |
                    |  (Glue Records)   |
                    +--------+----------+
                             |
              +--------------+--------------+
              v                              v
     +----------------+            +----------------+
     |    pantera      |            |     eagle       |
     |  149.102.139.33 |            |  167.86.92.97   |
     |  ns1.rbxsystems |            |  ns2.rbxsystems |
     |     .ch         |            |     .ch         |
     |                 |            |                 |
     |  PowerDNS Auth  |---AXFR--->|  PowerDNS Auth  |
     |  (primary)      |  NOTIFY   |  (secondary)    |
     |  gpgsql backend |            |  bind backend   |
     +-------+---------+            +-----------------+
             |
             | PostgreSQL (port 5432)
             v
     +----------------+
     |    jaguar       |
     |  161.97.147.76  |
     |  ParadeDB       |
     |  (db: pdns)     |
     +----------------+
```

### Full Node Map

| Node | IPv4 | IPv6 | Role | Group |
|------|------|------|------|-------|
| tiger | 158.220.116.31 | — | k3s server (control plane) | k3s_server |
| altaica | 173.212.246.8 | 2a02:c207:2319:6730::1 | k3s agent | k3s_agents |
| sumatrae | 5.189.178.212 | 2a02:c207:2319:6729::1 | k3s agent | k3s_agents |
| jaguar | 161.97.147.76 | — | k3s agent + Postgres state backend | k3s_agents, db_server |
| pantera | 149.102.139.33 | 2a02:c207:2256:6730::1 | ns1 — DNS primary | dns_servers |
| eagle | 167.86.92.97 | 2a02:c207:2252:7581::1 | ns2 — DNS secondary | dns_servers |

### Key Decisions

- **Software:** PowerDNS Authoritative 4.9 — Postgres-native backend, REST API, minimal config
- **Secondary uses bind backend, NOT gpgsql** — reduces jaguar dependency, keeps secondary functional without DB
- **dnsdist deferred to phase 2** — unnecessary for 2 zones with minimal traffic
- **DNS servers are dedicated** — no k3s workloads, no other services
- **DNSSEC deferred to phase 2**

### Zones

- `rbxsystems.ch` — institutional
- `strategos.gr` — product (already delegated to ns1/ns2.rbxsystems.ch)

### SOA Configuration

All zones use the following SOA parameters (phase 1):

```
@ IN SOA ns1.rbxsystems.ch. hostmaster.rbxsystems.ch. (
    2026040100  ; serial (YYYYMMDDNN format)
    3600        ; refresh (1h)
    900         ; retry (15m)
    604800      ; expire (7d)
    300         ; minimum / negative cache TTL (5m)
)
```

- **Serial format:** YYYYMMDDNN — increment NN for each change within the same day
- **MNAME:** ns1.rbxsystems.ch (primary)
- **RNAME:** hostmaster.rbxsystems.ch (mapped to hostmaster@rbxsystems.ch)

### Default TTLs

| Record Type | TTL | Rationale |
|-------------|-----|-----------|
| NS | 86400 (24h) | Stable, rarely changes |
| A / AAAA | 3600 (1h) | Default for web/service records |
| MX | 3600 (1h) | Standard for email |
| TXT (SPF/DMARC) | 3600 (1h) | Standard |
| CNAME (DKIM) | 3600 (1h) | Postmark-managed |
| SOA minimum | 300 (5m) | Negative caching |

During migration, consider lowering A/NS TTLs to 300s temporarily.

### Replication

- Primary (pantera) -> NOTIFY -> secondary (eagle)
- Eagle requests AXFR/IXFR from pantera
- ACL: only eagle IPs allowed for AXFR (**both IPv4 and IPv6**):
  - `167.86.92.97`
  - `2a02:c207:2252:7581::1`

### Delegation (registrar actions)

```
Registrar rbxsystems.ch -> glue records:
  ns1.rbxsystems.ch  ->  149.102.139.33 / 2a02:c207:2256:6730::1
  ns2.rbxsystems.ch  ->  167.86.92.97   / 2a02:c207:2252:7581::1

Registrar strategos.gr -> NS only (glue lives in .ch):
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
| finance@ | Alias | -> ceo@ |
| billing@ | Alias | -> finance@ |
| legal@ | Alias | -> ceo@ |
| sales@ | Alias | -> contact@ |
| partnerships@ | Alias | -> ceo@ |
| support@ | Alias | -> contact@ |
| hostmaster@ | Alias | -> ceo@ (SOA RNAME contact) |
| dmarc@ | Alias | -> ceo@ (DMARC aggregate reports) |

#### tx.rbxsystems.ch

| Address | Type | Destination |
|---------|------|-------------|
| no-reply@ | Sender only | No inbox. Outbound via Postmark |
| alerts@ | Sender only | No inbox. Outbound to internal team |

#### strategos.gr

| Address | Type | Destination |
|---------|------|-------------|
| support@ | Alias | -> contact@rbxsystems.ch (phase 1) |

#### tx.strategos.gr

| Address | Type | Destination |
|---------|------|-------------|
| no-reply@ | Sender only | No inbox. Outbound via Postmark |

### Usage Policy

- Human -> Human: root domain only (`@rbxsystems.ch`). Must accept replies.
- System -> Human: `tx.*` subdomains only. From: `no-reply@tx.*`.
- Marketing: NEVER on `tx.*`. Future: dedicated `mkt.*` subdomain.
- Alerts: `alerts@tx.rbxsystems.ch` -> internal only, subject prefix `[ALERT]`.
- Each new product gets its own `tx.{product}.{tld}` before sending any email.

### Postmark Servers

| Server Name | Domain | Use |
|-------------|--------|-----|
| RBX Institutional | rbxsystems.ch | contact@, ceo@ outbound |
| RBX Transactional | tx.rbxsystems.ch | no-reply@, alerts@ |
| Strategos Transactional | tx.strategos.gr | no-reply@ |

### DKIM Configuration

Each domain/subdomain verified in Postmark receives a unique DKIM CNAME.
Postmark provides the exact value after domain setup. All 4 must be configured:

| Domain | Record | Value |
|--------|--------|-------|
| rbxsystems.ch | `pm._domainkey.rbxsystems.ch` | CNAME provided by Postmark |
| tx.rbxsystems.ch | `pm._domainkey.tx.rbxsystems.ch` | CNAME provided by Postmark |
| strategos.gr | `pm._domainkey.strategos.gr` | CNAME provided by Postmark |
| tx.strategos.gr | `pm._domainkey.tx.strategos.gr` | CNAME provided by Postmark |

**Action required:** After creating Postmark servers, copy the exact DKIM CNAME values and add them to the zone files via pdnsutil or SQL on the primary.

### DMARC Cross-Domain Authorization

DMARC reports for `strategos.gr` are sent to `dmarc@rbxsystems.ch` (different domain).
Per RFC 7489 section 7.1, the receiving domain must authorize this with a TXT record:

```
strategos.gr._report._dmarc.rbxsystems.ch. TXT "v=DMARC1"
```

Without this record, mail providers will silently discard DMARC aggregate reports for strategos.gr.

---

## DNS Records

### rbxsystems.ch

```dns
$TTL 3600

; SOA
@               SOA     ns1.rbxsystems.ch. hostmaster.rbxsystems.ch. (
                        2026040100  ; serial (YYYYMMDDNN)
                        3600        ; refresh (1h)
                        900         ; retry (15m)
                        604800      ; expire (7d)
                        300         ; minimum / negative TTL (5m)
                )

; NS (TTL 86400)
@          86400 NS      ns1.rbxsystems.ch.
@          86400 NS      ns2.rbxsystems.ch.
ns1             A       149.102.139.33
ns1             AAAA    2a02:c207:2256:6730::1
ns2             A       167.86.92.97
ns2             AAAA    2a02:c207:2252:7581::1

; Web
@               A       158.220.116.31
www             CNAME   rbxsystems.ch.

; Email — root domain (institutional)
@               MX      10  inbound.postmarkapp.com.
@               TXT     "v=spf1 include:spf.mtasv.net ~all"
_dmarc          TXT     "v=DMARC1; p=none; rua=mailto:dmarc@rbxsystems.ch; fo=1"
pm._domainkey   CNAME   ; VALUE PROVIDED BY POSTMARK AFTER SETUP

; Email — transactional subdomain (tx.rbxsystems.ch)
; NOTE: tx.* subdomains are sender-only (no inbox, no web service).
; MX is required for Postmark to handle bounces/inbound processing.
; No A record — there is no web service on tx.rbxsystems.ch.
tx              MX      10  inbound.postmarkapp.com.
tx              TXT     "v=spf1 include:spf.mtasv.net ~all"
_dmarc.tx       TXT     "v=DMARC1; p=none; rua=mailto:dmarc@rbxsystems.ch; fo=1"
pm._domainkey.tx CNAME  ; VALUE PROVIDED BY POSTMARK AFTER SETUP

; DMARC cross-domain authorization (allow strategos.gr to send reports here)
strategos.gr._report._dmarc TXT "v=DMARC1"
```

### strategos.gr

```dns
$TTL 3600

; SOA
@               SOA     ns1.rbxsystems.ch. hostmaster.rbxsystems.ch. (
                        2026040100  ; serial (YYYYMMDDNN)
                        3600        ; refresh (1h)
                        900         ; retry (15m)
                        604800      ; expire (7d)
                        300         ; minimum / negative TTL (5m)
                )

; NS (TTL 86400)
@          86400 NS      ns1.rbxsystems.ch.
@          86400 NS      ns2.rbxsystems.ch.

; Web
@               A       158.220.116.31
www             CNAME   strategos.gr.

; Email — root domain
@               MX      10  inbound.postmarkapp.com.
@               TXT     "v=spf1 include:spf.mtasv.net ~all"
_dmarc          TXT     "v=DMARC1; p=none; rua=mailto:dmarc@rbxsystems.ch; fo=1"
pm._domainkey   CNAME   ; VALUE PROVIDED BY POSTMARK AFTER SETUP

; Email — transactional subdomain (tx.strategos.gr)
; NOTE: tx.* subdomains are sender-only. MX for Postmark bounce handling.
; No A record — there is no web service on tx.strategos.gr.
tx              MX      10  inbound.postmarkapp.com.
tx              TXT     "v=spf1 include:spf.mtasv.net ~all"
_dmarc.tx       TXT     "v=DMARC1; p=none; rua=mailto:dmarc@rbxsystems.ch; fo=1"
pm._domainkey.tx CNAME  ; VALUE PROVIDED BY POSTMARK AFTER SETUP
```

### Progressive Hardening

| Phase | SPF | DMARC | When |
|-------|-----|-------|------|
| 1 (now) | `~all` (softfail) | `p=none` (monitor) | Initial deployment |
| 2 | `-all` (hardfail) | `p=quarantine` | After 2-4 weeks of clean DMARC reports |
| 3 | `-all` | `p=reject` | After confirmed clean delivery |

---

## Migration Sequence (precise, ordered)

### Dependencies

```
Step 1-2 (add new nodes) has NO dependency on steps 3-7 (drain old nodes)
Step 3 DEPENDS ON step 2 (cluster healthy with new nodes)
Step 5 DEPENDS ON step 4 (both nodes drained)
Step 6 DEPENDS ON step 5 (nodes removed from k3s)
Step 7 DEPENDS ON step 6 (clean OS on pantera/eagle)
DNS deployment (step 8+) DEPENDS ON step 7
```

### Pre-flight Checks (before starting any phase)

```bash
# Check for PersistentVolumes bound to pantera or eagle
kubectl get pv -o wide | grep -E 'pantera|eagle'

# Check pods currently running on pantera and eagle
kubectl get pods -A -o wide | grep -E 'pantera|eagle'

# Check existing PodDisruptionBudgets
kubectl get pdb -A

# Verify bengal state (should be cordoned)
kubectl get node bengal -o jsonpath='{.spec.unschedulable}'

# Full backup of Postgres on jaguar
ssh root@161.97.147.76 "pg_dumpall -U postgres | gzip > /var/backups/pg_pre_migration_$(date +%Y%m%d).sql.gz"
```

**BLOCKER:** If PVs with local-path exist on pantera/eagle, data must be migrated before drain. Do NOT proceed with Phase B until resolved.

### Phase A — Expand cluster

**Step 1: Add altaica and sumatrae to k3s**

- Target: altaica (173.212.246.8), sumatrae (5.189.178.212)
- Run: `ansible-playbook site.yml --limit altaica,sumatrae` (hardening + k3s-agent)
- Result: cluster has 6 agents (eagle, pantera, bengal [cordoned], jaguar, altaica, sumatrae)

**Step 2: Validate cluster healthy**

- `kubectl get nodes` — all new nodes Ready
- `kubectl get pods -A` — no pods in CrashLoopBackOff or Pending
- Workloads scheduling correctly on altaica/sumatrae
- Wait for at least 1 full reconciliation cycle of ArgoCD

### Phase B — Drain and remove old nodes

**Step 2.5: Pause ArgoCD auto-sync**

```bash
# Pause all ArgoCD applications to prevent scheduling conflicts during drain
kubectl patch app <app-name> -n argocd --type merge -p '{"spec":{"syncPolicy":null}}'
```

**Step 3: Drain pantera**

```bash
kubectl drain pantera --ignore-daemonsets --delete-emptydir-data --timeout=300s
```

- Wait for all pods to reschedule
- Verify no pods remain on pantera except daemonsets
- Verify affected services are responding

**Step 4: Drain eagle**

```bash
kubectl drain eagle --ignore-daemonsets --delete-emptydir-data --timeout=300s
```

- Wait for all pods to reschedule
- Verify no pods remain on eagle except daemonsets
- Verify affected services are responding

**Step 5: Remove old nodes from k3s**

```bash
kubectl delete node pantera
kubectl delete node eagle
kubectl delete node bengal    # already cordoned/compromised since 2026-03-29
```

- Verify: `kubectl get nodes` shows only tiger, altaica, sumatrae, jaguar
- Verify: all workloads healthy after node removal

**Step 5.5: Re-enable ArgoCD auto-sync**

```bash
# Re-enable sync policies on all applications
```

**Step 6: Reinstall pantera and eagle**

- Reinstall clean Ubuntu 24.04 on pantera (149.102.139.33)
- Reinstall clean Ubuntu 24.04 on eagle (167.86.92.97)
- Verify SSH access with ed25519 key
- These machines must have NO k3s residue

### Phase C — DNS deployment

**Step 7: Deploy PowerDNS infrastructure**

```bash
# Create pdns database on jaguar
ansible-playbook site.yml --limit jaguar --tags pdns-database

# Harden DNS servers + deploy PowerDNS (serial: primary first, then secondary)
ansible-playbook site.yml --limit pantera,eagle
```

- pdns-database role creates db/user on jaguar with scoped pg_hba
- dns-hardening removes unnecessary ports 80/443, opens port 53
- pdns role installs PowerDNS with appropriate backend per host

**Step 8: Seed and validate DNS**

- Seed zone records via pdnsutil or SQL import on pantera
- Ensure SOA serial is set (format YYYYMMDDNN, e.g., 2026040100)
- Validate primary: `dig @149.102.139.33 rbxsystems.ch SOA`
- Validate secondary: `dig @167.86.92.97 rbxsystems.ch SOA`
- Validate AXFR: `dig @167.86.92.97 rbxsystems.ch AXFR` — SOA serial must match
- Validate both zones: repeat for strategos.gr

**Step 9: Update registrar delegation**

- Update glue records at .ch registrar (ns1/ns2.rbxsystems.ch)
- Update NS at .gr registrar
- Validate: `dig rbxsystems.ch NS +trace`
- Validate: `dig strategos.gr NS +trace`
- Monitor propagation: check from 8.8.8.8, 1.1.1.1, 9.9.9.9

### Phase D — Email deployment

**Step 10: Postmark setup**

- Create 3 Postmark servers (RBX Institutional, RBX Transactional, Strategos Transactional)
- Obtain DKIM CNAME values for all 4 domains/subdomains
- Add DKIM CNAME records to zones via pdnsutil on pantera
- Increment SOA serial after adding records
- Verify domains in Postmark dashboard
- Test send from each server
- Validate: `dig pm._domainkey.rbxsystems.ch CNAME`
- Validate: `dig pm._domainkey.tx.rbxsystems.ch CNAME`
- Validate: `dig pm._domainkey.strategos.gr CNAME`
- Validate: `dig pm._domainkey.tx.strategos.gr CNAME`

### Rollback Points

| After Step | Rollback |
|-----------|----------|
| 1-2 | Remove altaica/sumatrae from cluster, no impact |
| 3-4 | `kubectl uncordon pantera eagle` — pods reschedule back |
| 5 | Cannot rejoin old nodes trivially — must re-run k3s-agent role |
| 6 | Point of no return for old nodes — k3s removed via reinstall |
| 7-8 | DNS not yet delegated, no external impact |
| 9 | Revert glue records at registrar (propagation delay ~24-48h) |

---

## Ansible Structure

```
bootstrap/ansible/
├── inventory/hosts.yml          # Current topology
├── group_vars/
│   ├── all.yml                  # Global vars (non-sensitive)
│   ├── dns_servers.yml          # PowerDNS vars (zones, SOA, ACL)
│   └── vault.yml                # ENCRYPTED: all passwords and API keys
├── host_vars/
│   ├── pantera.yml              # pdns_role: primary, backend: gpgsql
│   └── eagle.yml                # pdns_role: secondary, backend: bind
├── roles/
│   ├── hardening/               # Base hardening (conditional: skips 80/443 for dns_servers)
│   ├── k3s-server/              # k3s control plane
│   ├── k3s-agent/               # k3s workers
│   ├── paradedb/                # Postgres on jaguar (scoped pg_hba per db/user)
│   ├── pdns-database/           # Creates pdns db/user on jaguar (least privilege)
│   ├── pdns/                    # Installs PowerDNS Auth (primary or secondary)
│   │   ├── tasks/main.yml
│   │   ├── handlers/main.yml
│   │   ├── defaults/main.yml
│   │   └── templates/
│   │       ├── pdns-primary.conf.j2
│   │       └── pdns-secondary.conf.j2
│   └── dns-hardening/           # DNS-specific firewall (port 53, removes 80/443)
└── site.yml                     # Ordered phases with dependency chain
```

### Inventory Groups

- `dns_servers`: pantera, eagle — dedicated DNS, no k3s
- `k3s_server`: tiger — control plane
- `k3s_agents`: altaica, sumatrae, jaguar — workers
- `db_server`: jaguar — Postgres (app dbs + pdns db)

### Security Model

**Hardening role (all nodes):**
- SSH hardened (key-only, fail2ban)
- UFW deny-by-default
- Ports 80/443 opened only for k3s nodes (conditional)
- Inter-node allow rules only for k3s cluster members

**DNS-hardening role (dns_servers only, runs after hardening):**
- Removes 80/443 rules left by base hardening
- Removes broad inter-node rules
- Opens port 53 UDP/TCP
- Allows DNS-to-DNS communication only
- Primary: allows outbound to jaguar:5432

**Postgres access (jaguar):**
- `paradedb` role: k3s nodes access their respective app databases only
- `pdns-database` role: pantera accesses pdns database only
- Eagle has NO Postgres access (uses bind backend)

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
| 5432 | TCP | 149.102.139.33 (pantera only) | PowerDNS -> Postgres |
| 5432 | TCP | k3s cluster nodes | App databases |

Eagle (167.86.92.97) does NOT have Postgres access — it uses bind backend.

### Backup

Daily `pg_dump` of `pdns` database on jaguar. 30-day retention.

### Monitoring

- `dig @pantera` and `dig @eagle` SOA serial comparison
- `dig +trace` from external
- Postmark dashboard for bounce/delivery rates
- DMARC aggregate reports to `dmarc@rbxsystems.ch`

---

## Pre-Rollout Checklist

### Before execution:

- [ ] `vault.yml` encrypted with ansible-vault
- [ ] SSH access verified to all nodes (altaica, sumatrae, pantera, eagle, jaguar, tiger)
- [ ] PersistentVolumes on pantera/eagle checked and migrated if needed
- [ ] PodDisruptionBudgets reviewed for critical workloads
- [ ] Full Postgres backup on jaguar completed
- [ ] Postmark account created with 3 servers configured
- [ ] DKIM CNAME values obtained from Postmark for all 4 domains
- [ ] Registrar credentials available for .ch and .gr
- [ ] `dmarc@rbxsystems.ch` alias/inbox configured (or ready to configure in Postmark)
- [ ] `hostmaster@rbxsystems.ch` alias configured
- [ ] Team notified of maintenance window

### During execution:

- [ ] Phase A: `--limit altaica,sumatrae` only
- [ ] Phase A: `kubectl get nodes` — confirm Ready
- [ ] Phase A: Wait 1 full ArgoCD sync cycle
- [ ] Phase B: Pause ArgoCD auto-sync
- [ ] Phase B: Drain one node at a time, verify between each
- [ ] Phase B: Confirm no service disruption after each drain
- [ ] Phase B: Delete nodes only after confirmed drain
- [ ] Phase B: Re-enable ArgoCD auto-sync
- [ ] Phase C: Test pantera→jaguar:5432 connectivity before PowerDNS
- [ ] Phase C: `dig @pantera` and `dig @eagle` — SOA match
- [ ] Phase C: Test AXFR: `dig @eagle rbxsystems.ch AXFR`
- [ ] Phase C: Update glue records — triple-check IPs before submitting
- [ ] Phase C: `dig rbxsystems.ch NS +trace` — confirm delegation
- [ ] Phase D: Add DKIM CNAMEs for all 4 domains, increment SOA serial
- [ ] Phase D: Verify in Postmark dashboard
- [ ] Phase D: Test send from each server

### After execution:

- [ ] DNS resolution from multiple external resolvers (8.8.8.8, 1.1.1.1, 9.9.9.9)
- [ ] SPF/DKIM/DMARC validation for all domains
- [ ] `kubectl get nodes` — correct topology (tiger, altaica, sumatrae, jaguar)
- [ ] `kubectl get pods -A` — no CrashLoopBackOff
- [ ] pg_dump cron job configured on jaguar for pdns database
- [ ] Monitor DMARC reports for 72h
- [ ] Monitor Postmark bounce/delivery rates
- [ ] Remove any stale DNS records pointing to old pantera/eagle IPs as k3s nodes

---

## Phases Summary

| Phase | Scope |
|-------|-------|
| **1 (now)** | PowerDNS primary/secondary, Postmark, Ansible roles, SPF ~all, DMARC p=none |
| **2** | dnsdist (rate-limiting), DNSSEC, 3rd NS (different region), Prometheus exporters, SPF -all, DMARC p=quarantine |
| **3** | DMARC p=reject, automated report parsing, possible own mail server |
