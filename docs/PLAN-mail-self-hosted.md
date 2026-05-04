# Self-Hosted Mail Server Plan

> Status: **Active — Phase 1 in progress** | Date: 2026-05-02 | Phase: 1 (Mailcow on lince)
>
> Extends and amends [`PLAN-dns-email-architecture.md`](PLAN-dns-email-architecture.md).
> Supersedes the "Postmark only — no MTA" policy for inbound on root (human) domains.
> Postmark remains the **outbound** path for all transactional and institutional sending.
>
> **For implementation continuation, see [`runbooks/MAIL-IMPLEMENTATION-STATUS.md`](runbooks/MAIL-IMPLEMENTATION-STATUS.md).**

## Current state (2026-05-04)

- ✓ VPS `lince` provisioned at Contabo (5.182.33.93 + 2a02:c207:2327:3864::1)
- ✓ Base hardening applied (ufw, fail2ban, SSH key-only)
- ✓ Ansible role `mailcow-host` written and committed (Docker, Mailcow bootstrap, firewall)
- ✓ Inventory + group/host vars in place
- ✓ Mailcow Dockerized running — 18 containers up, GUI at `https://mail.rbxsystems.ch/admin`
- ✓ Admin password set (stored in `pass`: `rbx/mail/admin-password`)
- ✓ Let's Encrypt cert issued for `mail.rbxsystems.ch`
- ✓ Postmark relay configured (outbound via `smtp.postmarkapp.com:587`)
- ✓ DNS records published on PowerDNS for both `rbxsystems.ch` and `strategos.gr`
- ✓ DKIM and Return-Path verified in Postmark for `rbxsystems.ch`
- ✓ MX cutover done — both domains → `mail.rbxsystems.ch`
- ✓ Inbound verified — Gmail → `contact@rbxsystems.ch` delivered
- ✓ Contabo PTR configured — both IPv4/IPv6 → `mail.rbxsystems.ch`
- ⧗ Postmark "RBX Institutional" server pending approval — outbound to external domains blocked. Support ticket in progress (response sent 2026-05-04).
- ⧗ Aliases not yet created in Mailcow GUI
- ⧗ DKIM/Return-Path for `strategos.gr` not yet configured
- ⧗ Backup configuration — off-site target TBD

---

## Scope of this document

Only the **deltas** versus the approved DNS/email plan are documented here. Anything not
mentioned (DNS topology, SOA, AXFR, address map, reputation isolation policy, progressive
hardening of SPF/DMARC) stays as defined in the parent plan.

What changes:

- A new dedicated VPS hosts the **inbound MTA + IMAP** for root (human) domains.
- MX records of `rbxsystems.ch` and `strategos.gr` flip from `inbound.postmarkapp.com` to
  the new MTA hostname.
- `tx.rbxsystems.ch` and `tx.strategos.gr` MX records **stay at Postmark Inbound** —
  these subdomains are sender-only; if a reply ever arrives, it lands in the Postmark
  Inbound webhook (or is dropped by null MX in Phase 2).
- SPF on root domains stays Postmark-only (`include:spf.mtasv.net`). The local MTA
  never sends to remote MXes directly — everything relays through Postmark, so the
  sending IP is always a Postmark IP and `mx` mechanism is unnecessary.
- New DNS records: `mail.{domain}` A/AAAA, `mta-sts.{domain}` A/AAAA + TXT, `_smtp._tls`
  TXT.
- New Ansible group `mail_servers` and role `mailcow-host`.

---

## Topology (after rollout)

```
                      +-------------------+
                      |    Registrars     |
                      +--------+----------+
                               |
         +---------------------+----------------------+
         v                                            v
+----------------+                          +----------------+
|    pantera     |                          |     eagle      |
|  PowerDNS auth |  ---AXFR/NOTIFY--->     |  PowerDNS auth |
|  (primary)     |                          |  (secondary)   |
+--------+-------+                          +----------------+
         |
         | resolves mail.rbxsystems.ch -> lince
         v
+--------------------------+         +--------------------------+
|         lince            |         |        Postmark          |
|   (NEW: 4 GB Contabo)    |         |   (SaaS — outbound)      |
|                          |         |                          |
|  Mailcow Dockerized:     |  <----  |  Servers:                |
|   - Postfix (MTA)        |  ENV    |   * RBX Institutional    |
|   - Dovecot (IMAP)       |  SMTP   |   * RBX Transactional    |
|   - Rspamd (anti-spam)   |  relay  |   * Strategos Trans.     |
|   - SOGo (webmail)       |  (opt)  |                          |
|   - ACME (Let's Encrypt) |         |  Inbound webhooks for    |
|                          |         |  tx.* subdomains         |
+--------------------------+         +--------------------------+

         Inbound mail flow:
           sender -> MX lookup -> mail.rbxsystems.ch (lince:25) -> Postfix
                                                              -> Rspamd filter
                                                              -> Dovecot LMTP
                                                              -> mailbox
         Outbound from contact@/ceo@:
           client (IMAP/SMTP submission 587) -> lince
                                              -> [option A] direct send
                                              -> [option B] relay via Postmark

         Outbound from no-reply@tx.*:
           Postmark API only (never touches lince)
```

Decision pending: outbound from human inboxes (`contact@`, `ceo@`) — direct send vs
Postmark relay. Recommendation: **relay through Postmark** (better deliverability, single
reputation lane). Local MTA only sends auto-generated mail (bounces, vacation replies).

---

## New host: `lince`

| Field | Value |
|-------|-------|
| Name | `lince` (lynx — fits the big-cat naming scheme; tiger, jaguar, pantera, eagle, altaica, sumatrae, bengal) |
| Provider | Contabo |
| Specs (proposed) | 4 vCPU, 8 GB RAM, 100 GB NVMe, Ubuntu 24.04 |
| IPv4 | `5.182.33.93` |
| IPv6 | `2a02:c207:2327:3864::1` (Contabo /64) |
| Location | Europe (same region as DNS servers) |
| Role | `mail_servers` group (new) |
| k3s | **Never** — dedicated mail host, no cluster workloads |

8 GB is the recommended minimum for Mailcow with Rspamd (Rspamd alone uses ~1.5 GB
under load). 4 GB works for low traffic but leaves no headroom for ClamAV (which
mailcow ships and which can spike to 1 GB resident). 8 GB is safer.

### Pre-flight on Contabo

One Contabo-specific item to handle **before** any deployment:

- **Reverse DNS (rDNS / PTR)**: Set in Contabo control panel for both IPv4 and IPv6.
  Must resolve to `mail.rbxsystems.ch`. Critical for Gmail/Outlook acceptance even
  when relaying through Postmark, because the local MTA's HELO/EHLO identity is still
  validated for some classes of mail.

### What we explicitly skip

- **Port 25 outbound unblock from Contabo**: not required. Confirmed 2026-05-02 by
  reachability test — Contabo's policy blocks outbound 25 by default, and our plan
  routes 100% of outbound through Postmark on port 587. Inbound port 25 is **not**
  blocked (verified: TCP RST on probe = network open, just no listener yet).

---

## Redundancy posture

### Phase 1: single MTA (`lince` only)

A single mail server is sufficient for institutional volume at this stage. SMTP has
built-in retry semantics that DNS does not: if `lince` is unreachable, sending MTAs
queue mail for 4-7 days before bouncing. A few hours of downtime causes delay, not
loss. This is fundamentally different from DNS, where a single failure causes
immediate user-visible breakage and the protocol provides no retry — hence the
mandatory NS pair on `pantera`/`eagle`.

For mail, the SMTP retry timer is the redundancy.

### Why no backup MX

Adding a secondary MX with higher preference (`MX 20 backup.example.`) used to be
common practice. **Do not do this.** Modern operational guidance treats backup MX as an
anti-pattern:

- Spammers target backup MXes specifically because they are often run with weaker
  filtering, then forward to the primary which trusts inbound from a "peer".
- Sending MTAs already retry the primary for days — a backup queue adds nothing real.
- Configuration drift between primary and backup is a recurring incident source.

The single MX `mail.rbxsystems.ch` is the correct configuration for Phase 1.

### When to add a second MTA

Triggers for promoting to a redundant pair:

- Customer SLA explicitly references email availability.
- Compliance (regulator, auditor) requires documented HA for institutional mail.
- Sustained volume saturates `lince` (CPU, RAM, queue depth — monitor in Grafana).
- Repeated multi-hour outages from the provider (track in incidents/).

None of these apply today.

### Future: HA pair (Phase 2+)

When a trigger fires, the upgrade path is:

1. Provision a second VPS in a different Contabo region (or a different provider for
   true geographic/AS diversity). Naming candidate: `puma`.
2. Mailcow's official "Hot Standby" mode (since 2023) provides:
   - Dovecot two-way replication for `vmail`
   - MariaDB Galera or async replication for Mailcow's metadata DB
   - Rspamd Redis sync
3. DNS publishes both as equal-priority MX:
   ```dns
   @ MX 10 mail.rbxsystems.ch.
   @ MX 10 mail2.rbxsystems.ch.
   ```
   Sending MTAs randomize between equal-priority MXes (RFC 5321 §5.1).
4. Each MTA terminates TLS independently; both must hold valid certs for both
   `mail.*` and `mail2.*` hostnames (or share a multi-SAN cert).
5. Outbound submission for human inboxes still relays through Postmark — unchanged.

Operational cost increase when the pair is live: ~€10/mo for the second VPS, plus
~30% more ops time (patching two hosts, monitoring replication lag, handling
split-brain on rare network partitions).

This section is documentation of the known upgrade path — **not a Phase 1
deliverable**.

---

## Software choice: Mailcow

### Rationale

Mailcow ships a complete, opinionated stack (Postfix, Dovecot, Rspamd, ACME, SOGo,
ClamAV, fail2ban) wired together via Docker Compose. It is the de-facto self-hosted
mail standard for small/medium operators — battle-tested, actively maintained, and
much cheaper to operate than building Postfix+Dovecot+Rspamd by hand.

### Alternatives considered

| Option | Pros | Cons | Decision |
|--------|------|------|----------|
| **Mailcow** | Complete stack, mature, large community, good docs | Docker complexity, Mailcow-specific layout | **Selected** |
| Stalwart | Single Rust binary, modern (JMAP), low footprint | Young (≈2y mature), small community, less tooling | Reject — too early for institutional use |
| Mailu | Similar to Mailcow, lighter | Smaller community, fewer features | Reject — Mailcow is more proven |
| Plain Postfix + Dovecot via Ansible | Maximum control, no Docker | Months of work to match Mailcow's anti-spam | Reject — premature optimization |
| Mail-in-a-Box | Easy install | Opinionated, less flexible, not made for ops-heavy use | Reject |

### Mailcow integration model

We **do not** package Mailcow inside our own Ansible role from scratch. Instead:

- Ansible role `mailcow-host` prepares the VPS (Docker Engine, firewall, swap, sysctls,
  unattended-upgrades, backup hooks, monitoring agent).
- Mailcow itself is installed via its official `git clone + ./generate_config.sh` flow,
  configured via `mailcow.conf` rendered from a Jinja2 template.
- Domain config (mailboxes, aliases) is applied via Mailcow's REST API from a separate
  task file, idempotent on subsequent runs.

This keeps the boundary clean: we own the host, Mailcow owns the mail stack.

---

## DNS deltas

All changes apply to the PowerDNS primary on `pantera`, via `pdnsutil` (gpgsql backend).
Increment SOA serial after each batch.

### `rbxsystems.ch` (changes)

```dns
; CHANGED — flip MX from Postmark to self-host
@               MX      10  mail.rbxsystems.ch.

; UNCHANGED from current — local MTA relays through Postmark, no direct send
@               TXT     "v=spf1 include:spf.mtasv.net ~all"

; NEW — MTA host
mail            A       5.182.33.93
mail            AAAA    2a02:c207:2327:3864::1

; NEW — MTA-STS (policy file served via HTTPS by Mailcow on lince)
mta-sts         A       5.182.33.93
mta-sts         AAAA    2a02:c207:2327:3864::1
_mta-sts        TXT     "v=STSv1; id=2026050201"
_smtp._tls      TXT     "v=TLSRPTv1; rua=mailto:tlsreports@rbxsystems.ch"

; NEW — autodiscover/autoconfig for mail clients (optional but quality-of-life)
autodiscover    CNAME   mail.rbxsystems.ch.
autoconfig      CNAME   mail.rbxsystems.ch.
```

DKIM (`pm._domainkey.rbxsystems.ch`) — added separately when Postmark is set up. Not
related to self-hosting.

DKIM for the local MTA's outbound (auto-replies, bounces): Mailcow generates its own
selector (`dkim._domainkey.rbxsystems.ch` by default; we will rename to a non-colliding
selector like `local._domainkey` to avoid confusion with Postmark's `pm._domainkey`).

### `strategos.gr` (changes)

Same pattern as `rbxsystems.ch`. `mail.strategos.gr` will be a CNAME to
`mail.rbxsystems.ch` — single MTA serves both domains via Mailcow's multi-domain
support.

```dns
@               MX      10  mail.strategos.gr.
@               TXT     "v=spf1 include:spf.mtasv.net ~all"
mail            CNAME   mail.rbxsystems.ch.
mta-sts         CNAME   mta-sts.rbxsystems.ch.
_mta-sts        TXT     "v=STSv1; id=2026050201"
_smtp._tls      TXT     "v=TLSRPTv1; rua=mailto:tlsreports@rbxsystems.ch"
```

Note: MX records cannot point to a CNAME. Therefore `mail.strategos.gr` MUST be A/AAAA,
not CNAME. Correction:

```dns
mail            A       5.182.33.93
mail            AAAA    2a02:c207:2327:3864::1
```

### `tx.rbxsystems.ch` and `tx.strategos.gr` — unchanged

Stay on Postmark Inbound. These subdomains never receive human mail. If we ever want
to fully forbid inbound, switch to null MX in Phase 2 (`tx MX 0 .`).

---

## Address routing matrix (post-rollout)

| Address | Inbound path | Outbound path |
|---------|--------------|---------------|
| `contact@rbxsystems.ch` | MX → lince → Dovecot mailbox | IMAP submit → Mailcow → relay via Postmark "RBX Institutional" |
| `ceo@rbxsystems.ch` | MX → lince → Dovecot mailbox | IMAP submit → Mailcow → relay via Postmark "RBX Institutional" |
| `hostmaster@rbxsystems.ch` | alias → ceo@ | n/a (alias) |
| `dmarc@rbxsystems.ch` | alias → ceo@ (or dedicated mailbox; see open questions) | n/a |
| `legal@`, `finance@`, `billing@`, `sales@`, `partnerships@`, `support@` | alias → contact@/ceo@ per parent plan | n/a |
| `no-reply@tx.rbxsystems.ch` | webhook → Postmark Inbound (or null MX in Phase 2) | Postmark API "RBX Transactional" |
| `alerts@tx.rbxsystems.ch` | same | Postmark API "RBX Transactional" |
| `support@strategos.gr` | MX → lince → forward to `contact@rbxsystems.ch` | Postmark relay (if reply needed) |
| `no-reply@tx.strategos.gr` | webhook → Postmark Inbound | Postmark API "Strategos Transactional" |
| auto-replies, bounces, vacation, postmaster | n/a | local MTA direct (port 25 outbound) |

The Postmark relay decision (column 3) is what keeps reputation concentrated on
Postmark's IPs. The local MTA only sends what it must (bounces, auto-replies). This is
the pattern most institutional self-hosters use.

---

## Anti-abuse stack (provided by Mailcow defaults)

- **Rspamd**: scoring, greylisting, RBL checks, DKIM verification of inbound, BIMI.
- **ClamAV**: attachment scanning. Memory-heavy — keep on but consider disabling in
  Phase 2 if mailbox volume stays low.
- **Postscreen**: pre-queue connection filtering. Drops the worst bots before they hit
  Postfix.
- **fail2ban**: built into Mailcow, blocks brute-force on SMTP/IMAP submission ports.
- **Recipient verification**: Postfix only accepts mail for known addresses (no
  catch-all wildcard).

DMARC verification of inbound is handled by Rspamd. If a sender fails DMARC, we apply
the sender's policy (quarantine to Junk for `p=quarantine`, reject for `p=reject`). This
is independent of our own outbound DMARC policy.

---

## TLS / certificate strategy

Mailcow's built-in ACME client handles certs for:

- `mail.rbxsystems.ch` (SMTP/IMAP/submission/HTTPS for SOGo)
- `mta-sts.rbxsystems.ch` (HTTPS for the policy file)
- `mail.strategos.gr` (SAN on the same cert or separate — Mailcow handles)
- `mta-sts.strategos.gr`
- `autodiscover.*`, `autoconfig.*` (SANs)

ACME challenges use HTTP-01 on port 80. Mailcow opens 80 only for ACME and redirects
otherwise. Renewal is automatic.

DANE/TLSA: deferred — requires DNSSEC, which the parent plan defers to Phase 2.

---

## Outbound strategy: Postmark relay (decided 2026-05-02)

All outbound from `lince` relays through Postmark via port 587 (submission with
STARTTLS). **No outbound port 25 is ever opened.** Postfix configuration on `lince`:

```
relayhost = [smtp.postmarkapp.com]:587
smtp_sasl_auth_enable = yes
smtp_sasl_password_maps = hash:/etc/postfix/sasl_passwd
smtp_use_tls = yes
smtp_tls_security_level = encrypt
```

Postmark Server Token doubles as both SASL username and password. Stored in
`vault.yml` (per existing pattern), rendered into `sasl_passwd` by the
`mailcow-host` role.

Scope of relay: **everything** — bounces, DSNs, auto-replies, postmaster mail, replies
from `contact@`/`ceo@`. The local MTA never speaks SMTP to remote MXes directly.

### Why this decision

- Contabo blocks outbound 25 by default; bypasses the unblock ticket entirely
- Postmark IPs are warmed; deliverability to Gmail/Outlook is solid from day one
- Single reputation lane — easier to monitor and reason about
- Cold IP reputation for `lince` would take 4-8 weeks to build for direct send

### Vendor lock-in trade-off (documented for awareness)

Total dependency on Postmark for all outbound:

- Postmark outage = `lince` cannot send anything (including delivery failure notifications)
- Every outbound (including bounces, auto-replies) consumes Postmark quota
- Postmark sees email content in plaintext during processing
- `lince`'s IP never warms — switching to direct send later is the same cold-start as today (waiting does not help reputation)

### Re-evaluation gate (Phase 2 trigger)

**After 60-90 days of stable operation**, explicitly decide whether to:

- (a) Stay on Option B (this plan) — default if no problems
- (b) Move to hybrid — system mail (bounces, postmaster) sends direct from `lince`,
  human mail stays on Postmark. Requires Contabo port 25 outbound unblock.
- (c) Move to Option A — full direct send. Requires unblock and 4-8 weeks of
  reputation warmup before cutover.

Without an explicit gate, inertia keeps Phase 1 forever. The gate forces a conscious
choice. Track in `docs/runbooks/` once `lince` is live.

---

## Backups

Mailcow ships a backup script (`helper-scripts/backup_and_restore.sh`) that snapshots:

- Postfix queues
- Dovecot vmail directory (the actual mailboxes)
- Rspamd state
- MySQL (Mailcow's metadata DB — local to the container, NOT jaguar)
- Redis state
- Configuration

Schedule: daily via systemd timer, retain 30 days local + sync to off-site (rsync.net or
Backblaze B2). Off-site target TBD — separate from the cluster's existing backup
strategy because mailcow's volume profile is different (large vmail tree, few writes per
file).

---

## Ansible structure (additions)

```
bootstrap/ansible/
├── inventory/hosts.yml           # add `mail_servers` group with `lince`
├── group_vars/
│   └── mail_servers.yml          # NEW — Mailcow config, hostname, ACME contact, etc.
├── host_vars/
│   └── lince.yml                 # NEW — IPs, mailcow_hostname
├── roles/
│   ├── mailcow-host/             # NEW
│   │   ├── tasks/
│   │   │   ├── main.yml
│   │   │   ├── docker.yml        # install Docker Engine + compose plugin
│   │   │   ├── firewall.yml      # ufw: 22, 25, 80, 443, 465, 587, 993, 995
│   │   │   ├── mailcow.yml       # clone repo, render mailcow.conf, ./generate_config, ./install
│   │   │   ├── domains.yml       # apply domain/mailbox/alias config via mailcow API
│   │   │   └── backup.yml        # systemd timer for backup_and_restore.sh + offsite sync
│   │   ├── handlers/main.yml
│   │   ├── defaults/main.yml
│   │   └── templates/
│   │       ├── mailcow.conf.j2
│   │       └── ufw-rules.j2
│   └── (existing roles unchanged)
└── site.yml                      # add new play: hosts: mail_servers, role: mailcow-host
```

### Inventory changes

```yaml
# bootstrap/ansible/inventory/hosts.yml (delta)
mail_servers:
  hosts:
    lince:
      ansible_host: <ipv4>
      ansible_host_ipv6: "<ipv6>"
```

### Group vars skeleton

```yaml
# bootstrap/ansible/group_vars/mail_servers.yml
mailcow_version: "2025-04"  # pin to release tag
mailcow_hostname: "mail.rbxsystems.ch"
mailcow_timezone: "Europe/Zurich"
mailcow_acme_contact: "hostmaster@rbxsystems.ch"
mailcow_admin_email: "ceo@rbxsystems.ch"
mailcow_install_dir: "/opt/mailcow-dockerized"

# Domains served (multi-domain)
mailcow_domains:
  - rbxsystems.ch
  - strategos.gr

# Mailboxes — passwords from vault.yml
mailcow_mailboxes:
  - { domain: rbxsystems.ch, local: contact, name: "RBX Contact" }
  - { domain: rbxsystems.ch, local: ceo,     name: "Leandro Damasio" }
  - { domain: rbxsystems.ch, local: dmarc,   name: "DMARC Reports" }

# Aliases
mailcow_aliases:
  - { from: "hostmaster@rbxsystems.ch", to: "ceo@rbxsystems.ch" }
  - { from: "legal@rbxsystems.ch",      to: "ceo@rbxsystems.ch" }
  - { from: "finance@rbxsystems.ch",    to: "ceo@rbxsystems.ch" }
  - { from: "billing@rbxsystems.ch",    to: "ceo@rbxsystems.ch" }
  - { from: "sales@rbxsystems.ch",      to: "contact@rbxsystems.ch" }
  - { from: "partnerships@rbxsystems.ch", to: "ceo@rbxsystems.ch" }
  - { from: "support@rbxsystems.ch",    to: "contact@rbxsystems.ch" }
  - { from: "support@strategos.gr",     to: "contact@rbxsystems.ch" }

# Outbound relay through Postmark (Option B — see plan)
mailcow_relay_enabled: true
mailcow_relay_host: "smtp.postmarkapp.com:587"
mailcow_relay_user: "<postmark-server-token-from-vault>"
mailcow_relay_pass: "<same>"  # Postmark uses the token as both user and pass
```

Secrets (mailbox passwords, Postmark relay token, Mailcow admin password) live in
`vault.yml`, generated from `pass` per the existing convention. No secrets in the
committed repo.

### Firewall on `lince`

| Port | Proto | Source | Purpose |
|------|-------|--------|---------|
| 22 | TCP | Any (fail2ban) | SSH |
| 25 | TCP | Any | Inbound SMTP (MX) — outbound 25 stays closed (relay via Postmark on 587) |
| 80 | TCP | Any | ACME HTTP-01 + MTA-STS HTTPS redirect |
| 443 | TCP | Any | SOGo webmail, MTA-STS, Mailcow UI |
| 465 | TCP | Any | SMTPS (SOGo / clients) |
| 587 | TCP | Any | Submission (clients) |
| 993 | TCP | Any | IMAPS |
| 995 | TCP | Any | POP3S (optional — disable if unused) |
| Default | * | * | **DENY** |

No port 53 (lince is not a DNS server). No port 5432 outbound to jaguar (Mailcow uses
its own MySQL inside Docker — does NOT reuse paradedb).

---

## Migration sequence

**Pre-conditions** (must all be satisfied before Step 1):

- [ ] Contabo VPS provisioned, IPv4 + IPv6 confirmed
- [ ] Port 25 outbound unblock approved by Contabo
- [ ] PTR for IPv4 set to `mail.rbxsystems.ch`
- [ ] PTR for IPv6 set to `mail.rbxsystems.ch`
- [ ] SSH access via ed25519 key confirmed (`ansible-playbook --check` passes hardening role)
- [ ] Postmark "RBX Institutional" server created (already required by parent plan)
- [ ] Postmark Server API Token available for the relay (in `pass`)

### Phase 1 — Host bootstrap (no DNS impact)

**Step 1**: Add `lince` to inventory under new `mail_servers` group. Run base hardening:

```bash
ansible-playbook site.yml --limit lince --tags hardening
```

**Step 2**: Run `mailcow-host` role to install Docker, firewall, and Mailcow:

```bash
ansible-playbook site.yml --limit lince --tags mailcow-host
```

Expect: Mailcow up, accessible at `https://<lince-ip>` with self-signed cert (DNS not yet
pointing here). Validate: `docker compose ps` in `/opt/mailcow-dockerized` shows all
containers healthy.

### Phase 2 — DNS records for the MTA (still no MX cutover)

**Step 3**: Add `mail.rbxsystems.ch` A/AAAA, `mta-sts.rbxsystems.ch` records, and the
`autodiscover`/`autoconfig` CNAMEs. Increment SOA serial.

```bash
ssh root@pantera 'pdnsutil add-record rbxsystems.ch mail A 3600 5.182.33.93'
ssh root@pantera 'pdnsutil add-record rbxsystems.ch mail AAAA 3600 2a02:c207:2327:3864::1'
# ... repeat for mta-sts, autodiscover, autoconfig
ssh root@pantera 'pdnsutil increase-serial rbxsystems.ch'
```

Validate from external resolver:

```bash
dig +short mail.rbxsystems.ch A @1.1.1.1
dig +short mail.rbxsystems.ch AAAA @1.1.1.1
```

**Step 4**: Trigger Mailcow ACME — once `mail.rbxsystems.ch` resolves publicly, ACME
gets real certs:

```bash
ssh root@lince 'cd /opt/mailcow-dockerized && docker compose exec acme-mailcow /srv/acme/acme.sh --force'
```

Validate: `https://mail.rbxsystems.ch` serves a valid Let's Encrypt cert.

### Phase 3 — Mailbox + alias provisioning

**Step 5**: Apply domains/mailboxes/aliases via the Mailcow API:

```bash
ansible-playbook site.yml --limit lince --tags mailcow-domains
```

Validate: log in to SOGo at `https://mail.rbxsystems.ch/SOGo` with `contact@rbxsystems.ch`.

**Step 6**: Send a test inbound — point a manual test send (from a personal Gmail) to
`contact@rbxsystems.ch` **after** modifying `/etc/hosts` locally to override MX to lince.
This verifies inbound works end-to-end before the public MX cutover.

### Phase 4 — MX cutover

**Step 7**: Lower TTL on the current MX records 24h before cutover (to reduce stale
caching).

```bash
ssh root@pantera 'pdnsutil replace-rrset rbxsystems.ch @ MX "10 inbound.postmarkapp.com." 300'
ssh root@pantera 'pdnsutil increase-serial rbxsystems.ch'
```

Wait 24 hours for the lower TTL to propagate.

**Step 8**: Flip MX to lince. Update SPF in the same change:

```bash
ssh root@pantera 'pdnsutil replace-rrset rbxsystems.ch @ MX "10 mail.rbxsystems.ch."'
ssh root@pantera 'pdnsutil replace-rrset rbxsystems.ch @ TXT "\"v=spf1 include:spf.mtasv.net mx ~all\""'
ssh root@pantera 'pdnsutil increase-serial rbxsystems.ch'
# Same for strategos.gr
```

**Step 9**: Validate end-to-end with multiple external senders:

- Gmail → contact@rbxsystems.ch → check arrives in SOGo
- Outlook.com → ceo@rbxsystems.ch → check arrives, check Authentication-Results header
  shows `dmarc=pass`, `spf=pass`, `dkim=pass`
- mail-tester.com — target 9/10 minimum (10/10 requires DNSSEC/DANE which is Phase 2)

**Step 10**: Restore MX TTL to default (3600) once stable.

### Rollback points

| After Step | Rollback action | Impact |
|-----------|-----------------|--------|
| 1-2 | Decommission lince, no DNS impact | None |
| 3-4 | Remove A/AAAA records (revert serial), no MX impact | None |
| 5 | Same as 3-4 — mailboxes are local to lince | None |
| 7 | Restore MX TTL — but TTL was only lowered, no behavior change | None |
| 8 | `pdnsutil replace-rrset` MX back to `inbound.postmarkapp.com` | Mail delivery resumes via Postmark Inbound; ~1h propagation |
| 9-10 | Same as 8 | Same as 8 |

The MX flip in Step 8 is the only externally visible change. Rollback is one
`replace-rrset` call away as long as Postmark Inbound stays configured for the domains
(which it will — we keep the Postmark setup running in parallel during cutover).

---

## Open questions

These need explicit decisions before implementation. Marking each as **DECIDE** for
operator review.

1. **DECIDE: VPS name** — `lince` (proposed) or other big-cat name? Available: lince, puma,
   leopardo, ocelot, caracal, serval. Confirm so the hostname goes into PTR / TLS cert /
   inventory consistently.
2. **DECIDE: VPS specs** — 4 vCPU / 8 GB / 100 GB NVMe (recommended) vs 4 vCPU / 4 GB /
   50 GB (budget). 8 GB is safer with ClamAV; 4 GB works if ClamAV is disabled.
3. ~~DECIDE: Outbound from human inboxes — direct send vs relay~~ **DECIDED 2026-05-02:**
   relay everything through Postmark (Option B). See "Outbound strategy" section.
   Re-evaluation gate at 60-90 days after launch.
4. **DECIDE: ClamAV on or off?** — On = 1 GB RAM overhead, malware protection;
   off = saves RAM, no protection beyond Rspamd. Recommendation: on for Phase 1, revisit
   after 30 days of metrics.
5. **DECIDE: Off-site backup target** — rsync.net, Backblaze B2, Storj, or other?
   Mailbox data is sensitive; encryption at rest required. Recommendation: rsync.net
   (mature, audit trail, deduplication).
6. **DECIDE: `dmarc@rbxsystems.ch`** — dedicated mailbox (visible inbox in SOGo) or
   alias to `ceo@`? Aggregate reports volume can be high (kilobytes per report, several
   per day per domain). Recommendation: dedicated mailbox so reports don't clutter
   `ceo@`.
7. **DECIDE: SOGo enabled or disabled?** — SOGo is Mailcow's webmail. If you only use
   IMAP clients (Apple Mail, Thunderbird), disabling SOGo saves resources and reduces
   attack surface. Recommendation: disable SOGo until needed.
8. **DECIDE: Catch-all aliases** — should anything not matching a known address bounce,
   or forward to a default mailbox? Recommendation: reject unknown (no catch-all),
   matches the parent plan's hygiene policy.

---

## Phase positioning vs parent plan

The parent plan (`PLAN-dns-email-architecture.md`) lists phases as:

| Phase | Original scope | Updated scope |
|-------|----------------|---------------|
| 1 (now) | Postmark only, no MTA | **Postmark + self-hosted inbound MTA on lince** |
| 2 | dnsdist, DNSSEC, 3rd NS, SPF -all, DMARC p=quarantine | + DANE/TLSA on MTA, MTA-STS enforce mode, outbound DKIM rotation policy |
| 3 | DMARC p=reject, automated report parsing, possible own mail server | DMARC p=reject, automated DMARC parsing pipeline (use the dmarc@ mailbox as input) |

Self-hosted mail server is **promoted from Phase 3 to Phase 1** in this amendment.

---

## Pre-rollout checklist (mail-specific)

Before Phase 1 Step 1:

- [x] VPS contracted, IPv4 (`5.182.33.93`) and IPv6 (`2a02:c207:2327:3864::1`) assigned
- [x] Inbound port 25 reachability confirmed (Contabo open at network level — verified 2026-05-02)
- [x] SSH public key bootstrapped, base hardening applied (verified 2026-05-02)
- [ ] PTR set for both IPs to `mail.rbxsystems.ch` — non-blocking; Contabo default `vmi3273864.contaboserver.net` is FCrDNS-valid for Phase 1; ticket to be opened in parallel
- [ ] `vault.yml` regenerated with: `mailcow_admin_password`, mailbox passwords, Postmark Server Token for relay
- [ ] DNS for `mail.rbxsystems.ch` not yet added (Step 3 adds it)
- [ ] Postmark "RBX Institutional" server confirmed configured (parent plan dep)
- [ ] Backup target account opened (rsync.net or alternative)

During execution:

- [ ] Phase 1: Mailcow containers all healthy after install
- [ ] Phase 2: ACME issues real cert without rate-limit warning
- [ ] Phase 3: Test mailbox login via IMAP and SOGo
- [ ] Phase 3: Inbound test from external sender (with /etc/hosts override) lands in mailbox
- [ ] Phase 4 Step 7: TTL lowered 24h before cutover
- [ ] Phase 4 Step 8: MX cutover during low-traffic window (weekend morning UTC)
- [ ] Phase 4 Step 9: mail-tester.com >= 9/10
- [ ] Phase 4 Step 9: Authentication-Results headers show pass on Gmail and Outlook

After execution:

- [ ] Daily Mailcow backup running (`systemctl status mailcow-backup.timer`)
- [ ] Off-site sync running and verified (restore-test from off-site within 30 days)
- [ ] Monitor Postmark dashboard for any drop in outbound (relay should be steady)
- [ ] Monitor `/var/log/mail.log` on lince for unexpected reject patterns
- [ ] Monitor DMARC reports landing in `dmarc@rbxsystems.ch`
