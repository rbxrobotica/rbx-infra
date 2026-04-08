# DNS Cutover — Implementation Guide

**Last updated:** 2026-04-08
**Purpose:** Operator handoff for resuming DNS cutover work in a new session.
This is not an architecture document. See `docs/infra/DNS.md` and `docs/infra/IAC-STRATEGY.md` for that.

---

## 1. Current state

### Execution plane (k3s)
| Node | IP | Role | Status |
|------|----|------|--------|
| tiger | 158.220.116.31 | control-plane | ✅ active |
| jaguar | 161.97.147.76 | agent + ParadeDB | ✅ active |
| altaica | 173.212.246.8 | agent | ✅ active |
| sumatrae | 5.189.178.212 | agent | ✅ active |
| bengal | 164.68.96.68 | — | ✅ removed |

### DNS plane
| Node | IP | Role | Status |
|------|----|------|--------|
| pantera | 149.102.139.33 | ns1 — pdns primary (gpgsql→jaguar) | ✅ active |
| eagle | 167.86.92.97 | ns2 — pdns secondary (bind+AXFR) | ✅ active |

Both pantera and eagle were drained from k3s and VPS-reinstalled before DNS provisioning.

### DNS zones
- `rbxsystems.ch` and `strategos.gr` created via OpenTofu (25 resources in state)
- AXFR replication confirmed: eagle mirrors both zones from pantera
- NS records in both zones point to ns1/ns2.rbxsystems.ch

### Registrar delegation
rbxsystems.ch cutover is complete. Infomaniak delegation was switched from ns11/ns12.infomaniak.ch to ns1/ns2.rbxsystems.ch and has propagated. `dig @8.8.8.8 rbxsystems.ch NS +trace` resolves through pantera/eagle.

strategos.gr delegation: pending (not yet changed at .gr registrar).

---

## 2. Local operator prerequisites

**OpenTofu binary:** `~/.local/bin/tofu` — installed standalone. There is no `terraform` in PATH.

**pdns API access:** The PowerDNS API on pantera listens only on `127.0.0.1:8081`. It is not publicly exposed. All `tofu plan/apply` runs require an active SSH tunnel:

```bash
ssh -f -N -L 127.0.0.1:18081:127.0.0.1:8081 root@149.102.139.33
```

Verify the tunnel is alive before running tofu: `curl -s http://127.0.0.1:18081/api/v1/servers -H "X-API-Key: <key>"` should return JSON, not a timeout.

**Local-only files (gitignored):**
- `infra/terraform/dns/terraform.tfvars` — contains `pdns_api_key` and real values
- `infra/terraform/dns/terraform.tfstate` — Terraform state, local backend

The state backend is local. If this workstation is lost, state is lost and zones would need to be imported or re-applied. Acceptable for single-operator use; migrate to S3 if that changes.

---

## 3. Boundaries of responsibility

| Layer | Tool | Scope |
|-------|------|-------|
| Host provisioning | Ansible (`bootstrap/ansible/`) | OS, ufw, PowerDNS install + config, pdns schema on jaguar |
| DNS record state | OpenTofu (`infra/terraform/dns/`) | Zones, all records within them |
| Glue + NS delegation | Registrar UI/API | One-time manual; not IaC |
| DKIM source | Postmark | Provides CNAME values; we create the records via tofu |

Do not use `pdnsutil` or direct SQL to modify zones in production. It creates drift that breaks the next `tofu apply`.

---

## 4. Important implementation discoveries

**pdns zones directory:** The systemd unit for pdns ships with `ProtectSystem=full`, which makes `/etc` read-only at runtime for the process. Zone files for the secondary (AXFR output) cannot be written to `/etc/powerdns/zones/`. Moved to `/var/lib/powerdns/zones/`. This is reflected in `roles/pdns/defaults/main.yml` (`pdns_zones_dir`). If re-provisioning eagle, Ansible will create the correct directory. Do not change it back to `/etc`.

**AXFR/NOTIFY IPv6 issue:** pantera sent NOTIFY to eagle from its IPv6 address (`2a02:c207:2256:6730::1`). Eagle's `named.conf` only listed the IPv4 master (`149.102.139.33`), so eagle refused the NOTIFY with "not a master". Fixed by adding the IPv6 address to the `masters { }` block in `named.conf.j2`. Both addresses are now listed.

**Infomaniak glue record input:** When adding glue records in the Infomaniak domain manager, enter relative names (`ns1`, `ns2`) — not FQDNs (`ns1.rbxsystems.ch.`). The interface appends the zone automatically.

**NS cutover prerequisite at Infomaniak:** Switching the NS delegation required that `ns1` and `ns2` A records already existed in the active Infomaniak zone. These were added as temporary records before changing the NS. After delegation propagates and our own zone serves them, those Infomaniak-side records become irrelevant.

---

## 5. Validation commands

```bash
# SOA on primary
dig @149.102.139.33 rbxsystems.ch SOA +short

# SOA on secondary — serial must match primary
dig @167.86.92.97 rbxsystems.ch SOA +short

# NS records on primary
dig @149.102.139.33 rbxsystems.ch NS +short

# NS records on secondary
dig @167.86.92.97 rbxsystems.ch NS +short

# A record on primary
dig @149.102.139.33 rbxsystems.ch A +short

# A record on secondary
dig @167.86.92.97 rbxsystems.ch A +short

# Public delegation trace — confirms registrar + glue
dig @8.8.8.8 rbxsystems.ch NS +trace

# Same for strategos.gr
dig @8.8.8.8 strategos.gr NS +trace

# Email records (once delegation is live)
dig @149.102.139.33 rbxsystems.ch MX +short
dig @149.102.139.33 rbxsystems.ch TXT +short
dig @149.102.139.33 _dmarc.rbxsystems.ch TXT +short
```

---

## 6. Temporary conditions / warnings

**⚠ pdns API key exposed:** The API key (`kWO5nWgAzqbnhm/A/+MM8VL7I3Dbakjg`) appeared in terminal output during this session when it was read from `/etc/powerdns/pdns.conf`. It must be rotated before continuing work. Procedure:
1. Generate a new key
2. Update `/etc/powerdns/pdns.conf` on pantera (`api-key=`)
3. Update `bootstrap/ansible/group_vars/all/vault.yml`
4. Update `infra/terraform/dns/terraform.tfvars`
5. Restart pdns on pantera: `systemctl restart pdns`

**DKIM records absent by design:** All four DKIM CNAME variables in `terraform.tfvars` are commented out. The Terraform resources use `count = var.dkim_X != "" ? 1 : 0`, so they are simply not created until values are provided. This is intentional — not a bug.

**.ch delegation complete:** rbxsystems.ch cutover propagated successfully. `dig @8.8.8.8 rbxsystems.ch NS +trace` resolves through ns1/ns2.rbxsystems.ch (pantera/eagle).

---

## 7. Next steps

In this order:

1. **Rotate pdns API key** (see warning above — key was exposed in session output)
2. **Change strategos.gr NS** at .gr registrar to ns1/ns2.rbxsystems.ch
3. **Configure Postmark** — create Sender Signatures for:
   - `rbxsystems.ch`
   - `tx.rbxsystems.ch`
   - `strategos.gr`
   - `tx.strategos.gr`
4. **Add DKIM values** to `infra/terraform/dns/terraform.tfvars`
5. **`~/.local/bin/tofu apply`** (tunnel must be open: `ssh -f -N -L 127.0.0.1:18081:127.0.0.1:8081 root@149.102.139.33`)
6. **Validate email records:** SPF reachable, DKIM CNAME resolves, DMARC `p=none` in place
7. Verify in Postmark: domain status should show SPF + DKIM confirmed
