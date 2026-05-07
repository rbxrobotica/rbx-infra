# Mail Self-Hosting — Implementation Status & Operations

> Self-contained runbook for the institutional mail stack.
> Read this together with [`docs/PLAN-mail-self-hosted.md`](../PLAN-mail-self-hosted.md)
> (architectural plan) and the parent [`docs/PLAN-dns-email-architecture.md`](../PLAN-dns-email-architecture.md).
>
> Last updated: 2026-05-07

---

## TL;DR — Where we are

- VPS `lince` (Contabo) provisioned, hardened, SSH key-only.
- Mailcow stack live (~18 containers up, all healthy).
- Postmark account approved; Sender Signature, DKIM, and Return-Path verified
  (green) for both `rbxsystems.ch` and `strategos.gr`.
- Postfix relay through Postmark `:587` configured and **codified in
  `mailcow-host` role** (`tasks/relay.yml` + templates). Idempotent.
- DNS cutover for both root domains has happened — MX points at
  `mail.rbxsystems.ch`. SPF, DKIM, DMARC, MTA-STS, TLSRPT, and
  pm-bounces records are live.
- End-to-end empirical proof (2026-05-07): test from `noreply@rbxsystems.ch`
  → `ldamasio@gmail.com` landed in Gmail Inbox with
  `dkim=pass spf=pass dmarc=pass` and TLS 1.3 client→relay→recipient.

The institutional mail send path is production-grade. Inbound delivery
to mailboxes is the next operational step (provisioning + GUI work).

---

## Decisions already made (do not re-litigate)

| Decision | Value | Rationale source |
|----------|-------|------------------|
| Outbound strategy | **Option B — relay everything through Postmark on port 587** | `PLAN-mail-self-hosted.md` §"Outbound strategy" |
| Stack | **Mailcow Dockerized** (not Stalwart, not Mailu, not raw Postfix) | `PLAN-mail-self-hosted.md` §"Software choice" |
| Redundancy | **Single MTA** (no backup MX, no HA pair) for Phase 1 | `PLAN-mail-self-hosted.md` §"Redundancy posture" |
| Mail VPS name | `lince` | Big-cat naming convention (tiger/jaguar/pantera/eagle/altaica/sumatrae) |
| Re-evaluation gate | 60-90 days post-launch — decide whether to stay on Option B or migrate | `PLAN-mail-self-hosted.md` §"Re-evaluation gate" |
| Relay credentials | Postmark Server Token, used as both SASL username and password | Postmark docs |
| Secrets boundary | Only the Server API Token is a secret (in `pass`); SPF / DKIM / Return-Path / DMARC are public DNS | This runbook |

---

## Infra coordinates

```
lince:
  IPv4:     5.182.33.93
  IPv6:     2a02:c207:2327:3864::1
  OS:       Ubuntu 24.04.4 LTS (initial hostname: vmi3273864)
  SSH:      key-only, root login, ~/.ssh/id_ed25519
  PTR v4:   mail.rbxsystems.ch (set 2026-05-03 via Contabo ticket #16240192404)
  PTR v6:   mail.rbxsystems.ch (set 2026-05-03 via Contabo ticket #16240192404)
  Group:    mail_servers (Ansible)
  Role:     mailcow-host
  Compose project: mailcowdockerized (in /opt/mailcow-dockerized)
```

---

## Architecture, in one diagram

```
                    Outbound from RBX accounts (Phase 1)

  user@rbxsystems.ch
        │ STARTTLS submission
        ▼
  ┌──────────────────────────┐         ┌─────────────────────┐
  │  Mailcow / Postfix       │  587    │                     │
  │  on lince (5.182.33.93)  │ ──────▶ │  smtp.postmarkapp   │
  │  relayhost = postmark    │  TLS    │  .com               │
  │  SASL: server token      │         │                     │
  └──────────────────────────┘         └──────────┬──────────┘
                                                   │
                                                   ▼ DKIM-signed by
                                            mta-NN-ord.mtasv.net
                                                   │
                                                   ▼
                                              recipient MX

                    Inbound to RBX accounts

   sender@example
        │
        ▼
  ┌────────────────────────────────────┐
  │  MX: mail.rbxsystems.ch (lince:25) │
  │  Mailcow Postfix → Dovecot         │
  └────────────────────────────────────┘
```

Auth headers expected on outbound (verified 2026-05-07):

- `dkim=pass header.i=@rbxsystems.ch` — RBX domain DKIM (selector is the
  timestamp-based one Postmark generated, e.g. `20260503181522pm`)
- `dkim=pass header.i=@pm.mtasv.net` — Postmark transit DKIM
- `spf=pass smtp.mailfrom=pm_bounces@pm-bounces.rbxsystems.ch`
- `dmarc=pass header.from=rbxsystems.ch` (alignment via DKIM)

---

## Day-to-day operations

### Reconcile the relay (token rotation, config drift)

```bash
cd /home/psyctl/apps/rbx-infra
# 1. Pull latest token from pass into vault.yml
bash bootstrap/scripts/init-vault-from-pass.sh
# 2. Re-render extra.cf + sasl_passwd, postmap, restart postfix-mailcow
ansible-playbook -i bootstrap/ansible/inventory/hosts.yml \
  --limit lince \
  bootstrap/ansible/site.yml \
  --tags relay
```

The role is idempotent: with no token change it is a no-op (zero changed
tasks). Restart of `postfix-mailcow` happens only if `extra.cf` or
`sasl_passwd` change.

### Send a one-off test from the host

```bash
ssh -i ~/.ssh/id_ed25519 root@5.182.33.93 \
  'printf "From: noreply@rbxsystems.ch\nTo: <recipient>\nSubject: relay test\n\nbody\n" |
   docker exec -i mailcowdockerized-postfix-mailcow-1 sendmail -f noreply@rbxsystems.ch <recipient>'

# Tail the relay step
ssh -i ~/.ssh/id_ed25519 root@5.182.33.93 \
  'docker logs mailcowdockerized-postfix-mailcow-1 --since 1m 2>&1 |
   grep -E "(relay=smtp.postmarkapp|status=)"'
```

A successful relay shows `status=sent (250 2.0.0 Ok: queued as <postmark-id>)`.

### Inspect the queue

```bash
ssh -i ~/.ssh/id_ed25519 root@5.182.33.93 \
  'docker exec mailcowdockerized-postfix-mailcow-1 mailq | tail -20'
```

### Check container health

```bash
ssh -i ~/.ssh/id_ed25519 root@5.182.33.93 \
  'cd /opt/mailcow-dockerized && docker compose ps'
```

Expect ~18 containers, mostly `Up` / `(healthy)`. The `dovecot-mailcow`
and `sogo-mailcow` containers do not expose a `healthy` status — `Up` is
sufficient.

---

## Repository layout (what lives where)

| Concern | Path |
|---|---|
| Mail role | `bootstrap/ansible/roles/mailcow-host/` |
| Relay tasks | `roles/mailcow-host/tasks/relay.yml` |
| Relay templates | `roles/mailcow-host/templates/{extra.cf.j2,sasl_passwd.j2}` |
| Role defaults | `roles/mailcow-host/defaults/main.yml` |
| Inventory | `bootstrap/ansible/inventory/hosts.yml` (`mail_servers` group) |
| Host vars | `bootstrap/ansible/host_vars/lince.yml` |
| Group vars | `bootstrap/ansible/group_vars/mail_servers.yml` |
| Vault (gitignored) | `bootstrap/ansible/group_vars/all/vault.yml` |
| Vault generator | `bootstrap/scripts/init-vault-from-pass.sh` |
| Architectural plan | `docs/PLAN-mail-self-hosted.md` |
| DNS context | `docs/PLAN-dns-email-architecture.md` |

---

## Pending operational items

### Provisioning (Mailcow GUI work, not IaC-managed yet)

Mail aliases, mailboxes, and per-domain settings are currently provisioned
manually through the Mailcow GUI at `https://mail.rbxsystems.ch/admin`.
Codifying this through Mailcow's REST API is a Phase-2 IaC item — not
blocking institutional sending.

- [ ] Create canonical mailboxes/aliases (`noreply@`, `dmarc@`, `tlsreports@`,
      operator inboxes) for `rbxsystems.ch`
- [ ] Same set for `strategos.gr`
- [ ] Decide whether to manage these via API (`tasks/domains.yml`) or
      keep GUI-managed for Phase 1

### Backup target decision

Mailcow data lives on a single VPS. Pick an off-site target
(rsync.net / Backblaze B2 / Storj / other) and write
`tasks/backup.yml` for the `mailcow-host` role.

---

## Reference: why the auth headers work

| Mechanism | Where | Value source | Gmail header value |
|---|---|---|---|
| SPF | DNS TXT on `rbxsystems.ch` | `v=spf1 include:spf.mtasv.net ~all` | `spf=pass smtp.mailfrom=pm_bounces@pm-bounces.rbxsystems.ch` |
| DKIM (RBX) | DNS CNAME at `<selector>._domainkey.rbxsystems.ch` → `pm.mtasv.net` | Postmark generates per-domain selector | `dkim=pass header.i=@rbxsystems.ch` |
| DKIM (transit) | always present, signed by Postmark itself | n/a | `dkim=pass header.i=@pm.mtasv.net` |
| Return-Path | DNS CNAME at `pm-bounces.rbxsystems.ch` → `pm.mtasv.net` | Postmark dashboard "Return-Path" | bounces routed back to Postmark for tracking |
| DMARC | DNS TXT at `_dmarc.rbxsystems.ch` | `v=DMARC1; p=none; rua=...` | `dmarc=pass (p=NONE) header.from=rbxsystems.ch` |
| MTA-STS | DNS A on `mta-sts.rbxsystems.ch` + `_mta-sts` TXT + `https://mta-sts.../.well-known/mta-sts.txt` | served by lince | inbound senders enforce TLS |
| TLSRPT | DNS TXT at `_smtp._tls.rbxsystems.ch` | `v=TLSRPTv1; rua=mailto:tlsreports@rbxsystems.ch` | aggregated TLS reports |

Same set is mirrored for `strategos.gr` (verified green in Postmark).

---

## Contabo quirks (do not re-discover)

1. **Reverse DNS Management panel does not work for our case.** IPv4 PTR
   is read-only in the panel; the IPv6 PTR form returns a generic 500
   ("We were unable to perform the request") for both compressed
   (`::1`) and expanded (`0000:0000:0000:0001`) IPv6 forms (confirmed
   2026-05-02). **Workaround: support ticket.** Reference template:

   ```
   Subject: Reverse DNS change request (IPv4 + IPv6)

   Hello,

   Please configure reverse DNS (PTR records) for the following addresses
   on VPS <ipv4> (<panel-hostname>):

     IPv4: <ipv4>                         -> <desired-ptr>
     IPv6: <ipv6>                         -> <desired-ptr>

   The panel ("Add PTR Record For An IPv6 Address" under DNS Management →
   Reverse DNS Management) returns "We were unable to perform the request"
   for both compressed and expanded IPv6 forms. IPv4 PTR is read-only in
   the panel.

   Use case: institutional mail server.

   Thank you.
   ```

   Concrete history:
   - **2026-05-02** ticket opened for `5.182.33.93` / `2a02:c207:2327:3864::1`.
   - **2026-05-03** Contabo Support (Ekaterina) confirmed both PTRs set
     to `mail.rbxsystems.ch` (ticket `#16240192404`, INT-13812980).
   - **2026-05-07** verified live via `dig +short -x` against both
     local and Google (8.8.8.8) resolvers; FCrDNS round-trip clean.

2. **Outbound port 25 is blocked by default.** Documented Contabo
   policy. With Option B (Postmark relay on port 587), this does not
   affect outbound delivery.

3. **Inbound port 25 is open.** Verified 2026-05-02 by TCP probe.

---

## Security note — secrets exposure during the writing of this plan

During the conversation that produced this plan, two Ansible commands inadvertently
printed the contents of `bootstrap/ansible/group_vars/all/vault.yml` to the conversation
transcript:

- `cat bootstrap/ansible/group_vars/all/*.yml`
- `ansible-inventory --list` (without filtering)

The following secrets appeared in the transcript and **should be rotated** as a
precaution if not yet done:

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
| Mailcow Ansible role | `bootstrap/ansible/roles/mailcow-host/` |
| Inventory & host-specific vars | `bootstrap/ansible/inventory/hosts.yml`, `bootstrap/ansible/host_vars/lince.yml` |
| Group vars (mail-specific) | `bootstrap/ansible/group_vars/mail_servers.yml` |
| Vault generation | `bootstrap/scripts/init-vault-from-pass.sh` |
| Secrets convention | `docs/infra/SECRETS.md` |

---

## When this runbook becomes obsolete

Archive (move to `docs/runbooks/archive/`) when:

- Off-site backup job is running daily with sync verified
- Mailbox / alias provisioning has been codified or formally accepted as
  GUI-managed for the long term
- A successor runbook covers ongoing mail-stack operations (incidents,
  upgrades, re-keying)

The architectural rationale will keep living in
`docs/PLAN-mail-self-hosted.md` even after this runbook retires.
