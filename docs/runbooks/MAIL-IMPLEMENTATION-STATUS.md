# Mail Self-Hosting — Implementation Status & Next Steps

> Self-contained runbook for continuing the institutional mail rollout.
> Read this together with [`docs/PLAN-mail-self-hosted.md`](../PLAN-mail-self-hosted.md)
> (architectural plan) and the parent [`docs/PLAN-dns-email-architecture.md`](../PLAN-dns-email-architecture.md).
>
> Last updated: 2026-05-04

---

## TL;DR — Where we are

- VPS `lince` (Contabo) provisioned, hardened, SSH key-only.
- **Mailcow running** — 18 containers up, GUI at `https://mail.rbxsystems.ch/admin` (Let's Encrypt cert).
- **MX cutover done** — `rbxsystems.ch` and `strategos.gr` MX → `mail.rbxsystems.ch`.
- **Inbound verified** — Gmail → `contact@rbxsystems.ch` delivered successfully.
- **Outbound blocked** — Postmark "RBX Institutional" server pending approval. Support ticket in progress (Amy @ Postmark requested use-case details, response sent 2026-05-04).
- **DKIM and Return-Path verified** in Postmark for `rbxsystems.ch`.
- Contabo PTR configured — both IPv4/IPv6 → `mail.rbxsystems.ch`.
- Mailboxes (`contact@`, `ceo@`, `dmarc@`) provisioned. Aliases pending.
- Next step: Postmark approval → verify outbound → aliases → DKIM for `strategos.gr`.

---

## Decisions already made (do not re-litigate)

| Decision | Value | Rationale source |
|----------|-------|------------------|
| Outbound strategy | **Option B — relay everything through Postmark on port 587** | `PLAN-mail-self-hosted.md` §"Outbound strategy" |
| Stack | **Mailcow Dockerized** (not Stalwert, not Mailu, not raw Postfix) | `PLAN-mail-self-hosted.md` §"Software choice" |
| Redundancy | **Single MTA** (no backup MX, no HA pair) for Phase 1 | `PLAN-mail-self-hosted.md` §"Redundancy posture" |
| Mail VPS name | `lince` | Big-cat naming convention (tiger/jaguar/pantera/eagle/altaica/sumatrae) |
| Re-evaluation gate | 60-90 days post-launch — decide whether to stay on Option B or migrate | `PLAN-mail-self-hosted.md` §"Re-evaluation gate" |

---

## Infra coordinates

```
lince:
  IPv4:     5.182.33.93
  IPv6:     2a02:c207:2327:3864::1
  OS:       Ubuntu 24.04.4 LTS
  SSH:      key-only, root login, ~/.ssh/id_ed25519
  PTR v4:   mail.rbxsystems.ch
  PTR v6:   mail.rbxsystems.ch
  Group:    mail_servers (Ansible)
  Role:     mailcow-host
```

---

## What's already done

### Repository changes

- `bootstrap/ansible/inventory/hosts.yml` — `mail_servers` group added with `lince`
- `bootstrap/ansible/host_vars/lince.yml` — created with `mailcow_hostname`
- `bootstrap/ansible/group_vars/mail_servers.yml` — created with domains list
- `bootstrap/ansible/roles/hardening/tasks/main.yml` — refactored to positive predicate `in (k3s_server + k3s_agents)`; correctly excludes `mail_servers`
- `bootstrap/ansible/roles/mailcow-host/` — full role created (defaults, tasks, handlers)
- `bootstrap/ansible/site.yml` — Phase 8: `Install mail server` play targeting `mail_servers`
- `docs/PLAN-mail-self-hosted.md` — architectural plan
- `docs/runbooks/MAIL-IMPLEMENTATION-STATUS.md` — this file

### Operational state on lince

- Base hardening: `ufw` (22, 25, 80, 443, 465, 587, 993, 995), SSH key-only, fail2ban
- **Mailcow Dockerized** (2026-05-02): 18 containers up, GUI at `https://mail.rbxsystems.ch/admin`
- Let's Encrypt cert for `mail.rbxsystems.ch` (R12, auto-renewed)
- Postfix port 25 listening
- Admin password in `pass`: `rbx/mail/admin-password`
- **Note**: Admin login is at `/admin`, not `/` (root is the user/mailbox login page)

### Postmark relay

- Outbound via `smtp.postmarkapp.com:587`, SASL auth
- Token in `pass`: `rbx/postmark/rbx-institutional-server-token`
- Config in `/opt/mailcow-dockerized/data/conf/postfix/extra.cf`, SASL creds in `sasl_passwd` (mode 0600)
- DKIM (`20260503181522pm._domainkey`) and Return-Path (`pm-bounces`) verified for `rbxsystems.ch`
- **Server pending Postmark approval** — outbound to external domains blocked

### DNS (PowerDNS on pantera)

Both `rbxsystems.ch` and `strategos.gr`:
- `mail` A `5.182.33.93`, AAAA `2a02:c207:2327:3864::1`
- `mta-sts` A/AAAA same
- `_mta-sts` TXT `"v=STSv1; id=2026050201"`
- `_smtp._tls` TXT `"v=TLSRPTv1; rua=mailto:tlsreports@rbxsystems.ch"`
- `autodiscover` CNAME `mail.rbxsystems.ch`
- `autoconfig` CNAME `mail.rbxsystems.ch`
- MX `10 mail.rbxsystems.ch` (TTL 3600)

`rbxsystems.ch` additionally:
- DKIM TXT `20260503181522pm._domainkey` (Postmark)
- Return-Path CNAME `pm-bounces` → `pm.mtasv.net` (Postmark)

### Mailboxes

- `contact@rbxsystems.ch`
- `ceo@rbxsystems.ch`
- `dmarc@rbxsystems.ch`

---

## What's pending

### Done

1. ~~Create Postmark "RBX Institutional" server~~ — Token in `pass`.
2. ~~Open Contabo support ticket for PTR~~ — Contabo configured both IPv4/IPv6 → `mail.rbxsystems.ch`.
3. ~~Bring Mailcow up~~ — 18 containers running.
4. ~~Add Postmark relay configuration~~ — Postfix relays via Postmark.
5. ~~Mailbox provisioning~~ — `contact@`, `ceo@`, `dmarc@` on `rbxsystems.ch`.
6. ~~DNS records on PowerDNS~~ — Both domains. Let's Encrypt cert. DKIM/Return-Path verified.
7. ~~MX cutover~~ — Both domains → `mail.rbxsystems.ch`. Inbound verified (Gmail → `contact@`).
8. ~~Contabo PTR~~ — Both IPv4/IPv6 → `mail.rbxsystems.ch`.

### Still pending

9. **Postmark server approval** — "RBX Institutional" pending approval. Outbound to external domains blocked. Amy @ Postmark requested use-case details 2026-05-03; response sent 2026-05-04.

10. **Outbound verification** — After Postmark approval: verify delivery to Gmail/Outlook. Run mail-tester.com ≥ 9/10.

11. **Aliases** — Create in Mailcow GUI:
    - `hostmaster@rbxsystems.ch` → `ceo@rbxsystems.ch`
    - `legal@rbxsystems.ch` → `ceo@rbxsystems.ch`
    - `finance@rbxsystems.ch` → `ceo@rbxsystems.ch`
    - `billing@rbxsystems.ch` → `ceo@rbxsystems.ch`
    - `sales@rbxsystems.ch` → `contact@rbxsystems.ch`
    - `partnerships@rbxsystems.ch` → `ceo@rbxsystems.ch`
    - `support@rbxsystems.ch` → `contact@rbxsystems.ch`
    - `support@strategos.gr` → `contact@rbxsystems.ch`

12. **DKIM/Return-Path for `strategos.gr`** — Add domain in Postmark, configure DNS on PowerDNS, verify.

13. **Backup configuration** — Write `tasks/backup.yml`. Off-site target TBD by operator.

14. **Secrets rotation** — vault.yml was exposed in a previous session. See "Security note" below.

---

## Verification checklist

- [x] All Mailcow containers healthy (`docker compose ps`)
- [x] GUI accessible at `https://mail.rbxsystems.ch/admin`
- [x] Mailcow admin password changed from default
- [x] Inbound port 25 listening
- [x] `ufw status` shows mail ports allowed
- [x] No errors in `journalctl -u docker.service`
- [x] Let's Encrypt cert issued for `mail.rbxsystems.ch`
- [x] Postfix relay configured (Postmark `smtp.postmarkapp.com:587`)
- [x] DNS records published on PowerDNS for both domains
- [x] DKIM and Return-Path verified in Postmark for `rbxsystems.ch`
- [x] MX cutover done — both domains → `mail.rbxsystems.ch`
- [x] Inbound verified — Gmail → `contact@rbxsystems.ch` delivered
- [x] Contabo PTR configured — both IPv4/IPv6 → `mail.rbxsystems.ch`
- [ ] Outbound verified (blocked on Postmark approval)
- [ ] mail-tester.com ≥ 9/10
- [ ] Aliases created
- [ ] DKIM/Return-Path for `strategos.gr`
- [ ] Backup job running daily

---

## Operator hand-list — items that need operator action

| # | Action | Where | When |
|---|--------|-------|------|
| 1 | Respond to Postmark approval request (Amy's email) | Email reply to Postmark | ASAP |
| 2 | Create aliases in Mailcow GUI | `https://mail.rbxsystems.ch/admin` | Anytime |
| 3 | Decide off-site backup target (rsync.net / Backblaze B2 / Storj / other) | — | Before backup step |
| 4 | Rotate exposed secrets — see "Security note" below | `pass` + re-run Ansible roles | High priority |

---

## Contabo quirks (do not re-discover)

1. **Reverse DNS panel is broken.** IPv4 PTR is read-only. IPv6 form returns 500 error. Open a support ticket instead.
2. **Default rDNS is FCrDNS-valid.** `vmi3273864.contaboserver.net` resolves forward to the same IP.
3. **Outbound port 25 is blocked by default.** With Postmark relay on 587, this does not affect us.
4. **Inbound port 25 is open.** Verified 2026-05-02.

---

## Security note — secrets exposure during the writing of this plan

During the conversation that produced this plan, two Ansible commands inadvertently
printed the contents of `bootstrap/ansible/group_vars/all/vault.yml` to the conversation
transcript:

- `cat bootstrap/ansible/group_vars/all/*.yml` (for inventory inspection)
- `ansible-inventory --list` (without filtering)

The following secrets appeared in the transcript and **should be rotated** as a
precaution:

- `paradedb_robson_password`
- `paradedb_truthmetal_password`
- `paradedb_robson_testnet_password`
- `pdns_db_password`
- `pdns_api_key`

Rotation procedure:

```bash
# For each affected secret:
# 1. Generate a new value
pass generate -i rbx/<secret-path> 32

# 2. Rebuild vault.yml
bash bootstrap/scripts/init-vault-from-pass.sh

# 3. Re-run the relevant role
ansible-playbook -i inventory/hosts.yml --limit jaguar site.yml --tags paradedb
ansible-playbook -i inventory/hosts.yml --limit jaguar site.yml --tags pdns-database
ansible-playbook -i inventory/hosts.yml --limit pantera,eagle site.yml --tags pdns
```

---

## Where to look for what

| Question | File |
|----------|------|
| Architecture / why these decisions | `docs/PLAN-mail-self-hosted.md` |
| DNS + Postmark broader context | `docs/PLAN-dns-email-architecture.md` |
| Step-by-step migration sequence | `docs/PLAN-mail-self-hosted.md` §"Migration sequence" |
| Mailcow Ansible role | `bootstrap/ansible/roles/mailcow-host/` |
| Inventory & host-specific vars | `bootstrap/ansible/inventory/hosts.yml`, `bootstrap/ansible/host_vars/lince.yml` |
| Group vars (mail-specific) | `bootstrap/ansible/group_vars/mail_servers.yml` |
| Secrets convention | `docs/infra/SECRETS.md` |

---

## When this runbook becomes obsolete

Delete or archive this file once:

- Mailcow is in production handling inbound for `rbxsystems.ch` and `strategos.gr`
- Postmark relay is configured and verified
- DNS cutover has happened and external mail-tester score is ≥ 9/10
- Backup job is running daily with off-site sync verified

At that point the only living document should be `docs/PLAN-mail-self-hosted.md` (as the
"why we did it this way" reference) and operational runbooks for incidents/maintenance.
