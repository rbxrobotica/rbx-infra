# DNS Operations

**Date:** 2026-04-07

For the full design rationale, zone record layout, and migration sequence see:
`docs/PLAN-dns-email-architecture.md`

This document covers operational procedures after DNS is live.

---

## Infrastructure

| Role | Host | IP | Backend |
|------|------|----|---------|
| Primary (ns1) | pantera | 149.102.139.33 | gpgsql → jaguar:5432 |
| Secondary (ns2) | eagle | 167.86.92.97 | bind (AXFR from pantera) |

Database: `pdns` on jaguar (161.97.147.76). Credentials in Ansible vault.

API: `http://127.0.0.1:8081` on pantera (local only). Reached via SSH tunnel.

---

## SSH tunnel to the PowerDNS API

The PowerDNS API listens **only on localhost** (`127.0.0.1:8081`) on pantera. It is not
exposed publicly. Terraform must reach this API to create zones and records.

The tunnel maps a local port on the workstation to pantera's localhost:

```
workstation:18081  ──[SSH]──►  pantera:127.0.0.1:8081
```

```bash
ssh -f -N -L 18081:127.0.0.1:8081 root@149.102.139.33
```

- `-f`: fork to background after authentication
- `-N`: no remote command (tunnel only)
- `-L 18081:127.0.0.1:8081`: bind local port 18081 → remote localhost:8081

Terraform's `pdns_api_url` is set to `http://127.0.0.1:18081`, which routes through
the tunnel to the pdns API. This is independent of Kubernetes — it is a direct SSH
connection to pantera the Linux host.

Kill the tunnel when done: `pkill -f 'ssh.*18081'`

---

## Managing records

All zone records are managed via Terraform. Do not use `pdnsutil` or direct SQL
to add or modify records in production — state drift will break the next `terraform apply`.

**Workflow for any record change:**

```bash
# 1. Open tunnel
ssh -f -N -L 18081:127.0.0.1:8081 root@149.102.139.33

# 2. Navigate to Terraform dir
cd infra/terraform/dns

# 3. Edit the relevant .tf file (rbxsystems_ch.tf or strategos_gr.tf)

# 4. Review
terraform plan

# 5. Apply
terraform apply
```

---

## Adding DKIM records after Postmark setup

1. Get the CNAME value from Postmark: Sender Signatures → your domain → DKIM.
2. Set in `terraform.tfvars`:
   ```
   dkim_rbxsystems_ch = "pm-xxxxxxxx.domainkey.postmarkapp.com."
   ```
3. `terraform apply` — creates the CNAME record.
4. Return to Postmark and click Verify.

DKIM variables for all four domains/subdomains are in `variables.tf`.

---

## Validation commands

```bash
# Check SOA on primary
dig @149.102.139.33 rbxsystems.ch SOA

# Check SOA on secondary — serial must match primary
dig @167.86.92.97 rbxsystems.ch SOA

# Check AXFR replication
dig @167.86.92.97 rbxsystems.ch AXFR

# Check public resolution (after registrar delegation)
dig @8.8.8.8 rbxsystems.ch NS
dig @1.1.1.1 rbxsystems.ch NS +trace

# Validate email records
dig @149.102.139.33 rbxsystems.ch MX
dig @149.102.139.33 rbxsystems.ch TXT
dig @149.102.139.33 _dmarc.rbxsystems.ch TXT
dig @149.102.139.33 pm._domainkey.rbxsystems.ch CNAME
```

Same patterns apply for strategos.gr.

---

## Registrar actions (one-time)

These are manual steps at the registrar. Not managed by IaC.

**rbxsystems.ch (.ch registrar — Infomaniak or SWITCH):**
Add glue records:
```
ns1.rbxsystems.ch  A     149.102.139.33
ns1.rbxsystems.ch  AAAA  2a02:c207:2256:6730::1
ns2.rbxsystems.ch  A     167.86.92.97
ns2.rbxsystems.ch  AAAA  2a02:c207:2252:7581::1
```
Set authoritative NS to: `ns1.rbxsystems.ch`, `ns2.rbxsystems.ch`

**strategos.gr (.gr registrar):**
Set NS to: `ns1.rbxsystems.ch`, `ns2.rbxsystems.ch`
(No glue needed — ns1/ns2 are in a different TLD)

---

## Progressive email hardening

SPF and DMARC start in monitoring mode. Tighten after 2-4 weeks of clean reports.

| Phase | SPF | DMARC | When |
|-------|-----|-------|------|
| 1 (now) | `~all` | `p=none` | Initial |
| 2 | `-all` | `p=quarantine` | After clean DMARC reports |
| 3 | `-all` | `p=reject` | After confirmed clean delivery |

Update records in the `.tf` files and run `terraform apply`.

---

## Troubleshooting

When something breaks, do not improvise here — use the dedicated
runbook:

- `docs/runbooks/DNS-TROUBLESHOOTING.md` — symptom matrix, SSH
  tunnel recovery, gpgsql password drift, AXFR replication lag.
- `docs/incidents/INCIDENT-2026-04-25-PDNS-CRASHLOOP.md` — case
  study from the FE-P1 launch where a vault password rotation
  was not propagated to pantera's `pdns.conf`.

Required reading before you operate DNS the first time.
