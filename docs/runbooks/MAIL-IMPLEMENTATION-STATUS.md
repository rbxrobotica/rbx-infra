# Mail Self-Hosting — Implementation Status & Next Steps

> Self-contained runbook for continuing the institutional mail rollout.
> Read this together with [`docs/PLAN-mail-self-hosted.md`](../PLAN-mail-self-hosted.md)
> (architectural plan) and the parent [`docs/PLAN-dns-email-architecture.md`](../PLAN-dns-email-architecture.md).
>
> Last updated: 2026-05-02

---

## TL;DR — Where we are

- VPS `lince` (Contabo) provisioned, hardened, SSH key-only.
- Ansible role `mailcow-host` written and committed; play added to `site.yml`.
- **Mailcow not yet installed.** Next step is a partial-mode bring-up (no Postmark relay yet).
- Two operator actions are blocking full launch: (1) Postmark Server Token, (2) Contabo PTR ticket.

---

## Decisions already made (do not re-litigate)

| Decision | Value | Rationale source |
|----------|-------|------------------|
| Outbound strategy | **Option B — relay everything through Postmark on port 587** | `PLAN-mail-self-hosted.md` §"Outbound strategy" |
| Stack | **Mailcow Dockerized** (not Stalwart, not Mailu, not raw Postfix) | `PLAN-mail-self-hosted.md` §"Software choice" |
| Redundancy | **Single MTA** (no backup MX, no HA pair) for Phase 1 | `PLAN-mail-self-hosted.md` §"Redundancy posture" |
| Mail VPS name | `lince` | Big-cat naming convention (tiger/jaguar/pantera/eagle/altaica/sumatrae) |
| Re-evaluation gate | 60-90 days post-launch — decide whether to stay on Option B or migrate | `PLAN-mail-self-hosted.md` §"Re-evaluation gate" |

---

## Infra coordinates

```
lince:
  IPv4:     5.182.33.93
  IPv6:     2a02:c207:2327:3864::1
  OS:       Ubuntu 24.04.4 LTS (initial hostname: vmi3273864)
  SSH:      key-only, root login, ~/.ssh/id_ed25519
  PTR v4:   vmi3273864.contaboserver.net (default; pending ticket to change to mail.rbxsystems.ch)
  PTR v6:   vmi3273864.contaboserver.net (default; pending ticket — see "Contabo PTR quirk" below)
  Group:    mail_servers (Ansible)
  Role:     mailcow-host
```

---

## What's already done

### Repository changes

- `bootstrap/ansible/inventory/hosts.yml` — `mail_servers` group added with `lince`
- `bootstrap/ansible/host_vars/lince.yml` — created with `mailcow_hostname`
- `bootstrap/ansible/group_vars/mail_servers.yml` — created with domains list
- `bootstrap/ansible/roles/hardening/tasks/main.yml` — refactored 4 conditionals from negative (`not in dns_servers`) to positive (`in (k3s_server + k3s_agents)`); semantically identical for existing groups, correctly excludes `mail_servers` from k3s rules
- `bootstrap/ansible/roles/mailcow-host/` — full role created:
  - `defaults/main.yml`
  - `tasks/main.yml`, `tasks/docker.yml`, `tasks/mailcow.yml`, `tasks/firewall.yml`
  - `handlers/main.yml`
- `bootstrap/ansible/site.yml` — Phase 8 added: `Install mail server` play targeting `mail_servers`
- `docs/PLAN-mail-self-hosted.md` — extension of parent plan, decisions documented
- `docs/runbooks/MAIL-IMPLEMENTATION-STATUS.md` — this file

### Operational state on lince

- Base hardening applied: `ufw` enabled (only port 22 v4+v6), SSH password auth disabled, `PermitRootLogin prohibit-password`, fail2ban active
- Inbound port 25 confirmed open at Contabo network level (TCP RST on probe = no listener but path open)

---

## What's pending

### Operator-blocking items

1. **Create Postmark "RBX Institutional" server**
   - URL: https://account.postmarkapp.com → Servers → Create Server
   - Name: `RBX Institutional`
   - Type: Transactional
   - After creation → API Tokens tab → copy the **Server API Token**
   - Store in `pass`: `pass insert rbx/postmark/rbx-institutional/server-token`
   - **Do NOT paste the token in any chat or file outside `pass`/`vault.yml`**

2. **Open Contabo support ticket for PTR**
   - Reason: panel bug — "Add PTR Record For An IPv6 Address" form returns "We were unable to perform the request" for both compressed (`::1`) and expanded (`0000:0000:0000:0001`) IPv6 forms. IPv4 PTR is read-only in panel.
   - Ticket text:
     ```
     Subject: Reverse DNS change request (IPv4 + IPv6)

     Hello,

     Please configure reverse DNS (PTR records) for the following addresses
     on VPS 5.182.33.93 (vmi3273864):

       IPv4: 5.182.33.93                    -> mail.rbxsystems.ch
       IPv6: 2a02:c207:2327:3864::1         -> mail.rbxsystems.ch

     I tried adding the IPv6 PTR via the panel ("Add PTR Record For An IPv6
     Address" under DNS Management → Reverse DNS Management) but received
     "We were unable to perform the request. Please retry or contact the
     support" with both compressed and expanded IPv6 forms.

     Use case: institutional mail server.

     Thank you.
     ```
   - **Non-blocking**: Phase 1 launch can proceed with default Contabo PTR (`vmi3273864.contaboserver.net` is FCrDNS-valid). With Option B + Postmark relay, lince's rDNS is rarely consulted.

### Implementation items (after the above)

3. **Bring Mailcow up in partial mode** (no relay yet) — verifies Docker/Compose installation and Mailcow bootstrap on lince. See "Step-by-step execution" below.

4. **Add Postmark relay configuration** — once token lands in `vault.yml`, write `tasks/relay.yml` in the `mailcow-host` role:
   - Render `data/conf/postfix/extra.cf` with `relayhost = [smtp.postmarkapp.com]:587` etc.
   - Render `data/conf/postfix/sasl_passwd` with the token (root-only mode 0600)
   - Run `postmap` inside the container
   - Restart `postfix-mailcow` service

5. **Mailbox & alias provisioning** — write `tasks/domains.yml` using Mailcow's REST API (admin token from mailcow.conf or GUI). Alternative for Phase 1: provision manually via the Mailcow GUI.

6. **DNS records on PowerDNS** — Phase 2 of the migration sequence in `PLAN-mail-self-hosted.md`:
   - `mail.rbxsystems.ch` A `5.182.33.93`, AAAA `2a02:c207:2327:3864::1`
   - `mta-sts.rbxsystems.ch` A/AAAA same
   - `_mta-sts.rbxsystems.ch` TXT `"v=STSv1; id=2026050201"`
   - `_smtp._tls.rbxsystems.ch` TXT `"v=TLSRPTv1; rua=mailto:tlsreports@rbxsystems.ch"`
   - `autodiscover.rbxsystems.ch` CNAME `mail.rbxsystems.ch`
   - `autoconfig.rbxsystems.ch` CNAME `mail.rbxsystems.ch`
   - Same set for `strategos.gr`
   - DKIM CNAMEs from Postmark for `pm._domainkey.{domain}` (Postmark gives the value after domain verification)
   - Increment SOA serial after each batch
   - Apply via `pdnsutil` on `pantera` (gpgsql backend, not zone files)

7. **MX cutover** — Phase 4 of the plan. Lower TTL 24h before, then flip MX from `inbound.postmarkapp.com` to `mail.rbxsystems.ch` for root domains. `tx.*` MX stays at Postmark Inbound.

8. **Backup configuration** — write `tasks/backup.yml`. **Open question**: off-site target. Plan recommends rsync.net but operator has not decided.

---

## Step-by-step execution: bring Mailcow up (partial mode)

This is the next concrete action. Run from a workstation with:
- SSH access to lince via `~/.ssh/id_ed25519`
- Ansible installed
- Repo cloned at the standard location

```bash
cd /home/psyctl/apps/rbx-infra/bootstrap/ansible

# 1. Smoke-test SSH via Ansible
ansible -i inventory/hosts.yml lince -m ping

# 2. Run the mail server play (will install Docker + Mailcow + open firewall)
ansible-playbook -i inventory/hosts.yml --limit lince site.yml

# 3. Verify on lince
ssh -i ~/.ssh/id_ed25519 root@5.182.33.93 'cd /opt/mailcow-dockerized && docker compose ps'
# Expected: ~15 containers, all "running" or "healthy"

# 4. Verify firewall
ssh -i ~/.ssh/id_ed25519 root@5.182.33.93 'ufw status numbered'
# Expected: ports 22, 25, 80, 443, 465, 587, 993, 995 ALLOW

# 5. Access GUI from local
# Browser: https://5.182.33.93/  (self-signed cert warning — accept; real cert later)
# Default admin login: admin / moohoo  ← CHANGE IMMEDIATELY in Mailcow → System → Admin
```

If `docker compose ps` shows containers in restart loops, check `docker compose logs --tail 100`. The most common first-run issue is host port 25 being held by Postfix on the host — Ubuntu 24.04 base is clean so this should not happen.

---

## Verification checklist after partial-mode bring-up

- [ ] All Mailcow containers healthy (`docker compose ps`)
- [ ] GUI accessible at `https://5.182.33.93`
- [ ] Mailcow admin password changed from default
- [ ] Inbound port 25 listening (`ss -tlnp | grep :25` on lince)
- [ ] `ufw status` shows mail ports allowed
- [ ] No errors in `journalctl -u docker.service` last 5 minutes

**Outbound is intentionally broken at this stage** (no relay configured). That gets fixed when the Postmark token lands.

---

## Operator hand-list — items that need operator action

| # | Action | Where | When |
|---|--------|-------|------|
| 1 | Create Postmark "RBX Institutional" server, capture token to `pass` | Postmark dashboard | Before relay step |
| 2 | Open Contabo ticket for PTR (text above) | my.contabo.com support | Anytime (non-blocking) |
| 3 | Decide off-site backup target (rsync.net / Backblaze B2 / Storj / other) | — | Before backup step |
| 4 | Decide on remaining open questions in `PLAN-mail-self-hosted.md` §"Open questions" | — | Before respective steps |
| 5 | After IaC implementation: rotate exposed secrets — see "Security note" below | `pass` + re-run respective Ansible roles | High priority |

---

## Contabo quirks (do not re-discover)

1. **Reverse DNS Management panel is read-only for IPv4.** Only IPv6 PTRs can be added via the form, and even that fails with a generic 500 error in some cases (confirmed 2026-05-02 with both compressed and expanded IPv6 formats). Do not waste time — open a support ticket.

2. **Default rDNS is FCrDNS-valid.** `vmi3273864.contaboserver.net` resolves forward to the same IP, so it does not break basic acceptance checks. With Option B (Postmark relay), the local MTA's rDNS is rarely consulted.

3. **Outbound port 25 is blocked by default.** This was confirmed earlier and is documented Contabo policy. With Option B (Postmark relay on port 587), this does not affect us.

4. **Inbound port 25 is open.** Verified via TCP probe 2026-05-02: `nc` returns "connection refused" (TCP RST), not timeout — meaning packets reach the host, just no listener yet.

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

Operational impact: short PowerDNS API key change is transparent. ParadeDB password
change requires app restart on consumer side (robson, truthmetal in cluster). Schedule
during low-traffic window if production traffic is active.

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
| ArgoCD / cluster patterns | `docs/ARGOCD-BEST-PRACTICES.md` (not used by mail role, but cluster context) |

---

## When this runbook becomes obsolete

Delete or archive this file once:

- Mailcow is in production handling inbound for `rbxsystems.ch` and `strategos.gr`
- Postmark relay is configured and verified
- DNS cutover has happened and external mail-tester score is ≥ 9/10
- Backup job is running daily with off-site sync verified

At that point the only living document should be `docs/PLAN-mail-self-hosted.md` (as the
"why we did it this way" reference) and operational runbooks for incidents/maintenance.
