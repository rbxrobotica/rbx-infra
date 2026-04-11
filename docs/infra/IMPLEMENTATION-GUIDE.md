# DNS Cutover — Implementation Guide

**Last updated:** 2026-04-10
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
ssh -o ExitOnForwardFailure=yes -f -N -L 127.0.0.1:18081:127.0.0.1:8081 root@149.102.139.33
```

Verify before running tofu (see sections 6 and 7 for validation and secrets details). Then invoke tofu via the wrapper — never call it directly:

```bash
~/apps/rbx-infra/scripts/dns-tofu-env.sh ~/.local/bin/tofu plan
~/apps/rbx-infra/scripts/dns-tofu-env.sh ~/.local/bin/tofu apply
```

**Local-only files (gitignored):**
- `infra/terraform/dns/terraform.tfvars` — non-secret runtime values (k3s IP, DKIM CNAMEs)
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

**UFW FORWARD chain blocks k3s overlay (flannel VXLAN):** When a new k3s agent joins the cluster, the Ansible hardening role sets `ufw policy deny` which sets the FORWARD chain to DROP. This breaks flannel VXLAN: decapsulated packets can't be forwarded to pods, and return VXLAN packets from new nodes aren't allowed at existing nodes. Two fixes are required:

1. `ufw default allow routed` on every k3s node (the Ansible role now does this automatically)
2. New agent IPs must be in the `ALLOW IN` rules of every existing node — the Ansible role handles this via `groups['k3s_agents']`, but only for nodes that were in the inventory when the play last ran. Re-run the hardening role against all existing nodes after adding a new agent.

**Adding a new agent checklist (after node joins k3s):**
```bash
# From workstation — re-run hardening on all existing nodes to add new agent's IP
ansible-playbook -i inventory/hosts.yml site.yml --tags hardening
```

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

## 6. Secrets and PowerDNS API access

### Key storage

The PowerDNS API key lives exclusively in `pass`:

```bash
pass rbx/dns/pdns-api-key
```

It is **not** stored in `terraform.tfvars`, environment files, or any file tracked by Git. The Ansible vault copy (`bootstrap/ansible/group_vars/all/vault.yml`) remains authoritative for provisioning.

### Why not TF_VAR_?

The `pan-net/powerdns` provider reads `PDNS_API_KEY` and `PDNS_SERVER_URL` directly as provider-native env vars, bypassing the Terraform variable system. Using `TF_VAR_pdns_api_key` instead would create a precedence trap: any value in `terraform.tfvars` silently overrides `TF_VAR_*`. Provider-native vars have no such issue.

### Wrapper script

All `tofu` invocations must go through the wrapper:

```bash
~/apps/rbx-infra/scripts/dns-tofu-env.sh ~/.local/bin/tofu plan
~/apps/rbx-infra/scripts/dns-tofu-env.sh ~/.local/bin/tofu apply
```

The wrapper:
1. Calls `pass rbx/dns/pdns-api-key` to read the key
2. Exports `PDNS_API_KEY` and `PDNS_SERVER_URL`
3. `exec`s the given command with those variables in scope

### SSH tunnel requirement

The PowerDNS API on pantera listens only on `127.0.0.1:8081` — not publicly exposed. Open the tunnel before running the wrapper:

```bash
ssh -o ExitOnForwardFailure=yes -f -N -L 127.0.0.1:18081:127.0.0.1:8081 root@149.102.139.33
```

`ExitOnForwardFailure=yes` makes the command fail immediately if the port-forward cannot be established, rather than hanging.

### Validate API before running tofu

```bash
curl -i http://127.0.0.1:18081/api/v1/servers -H "X-API-Key: $(pass rbx/dns/pdns-api-key)"
```

Expected: `HTTP/1.1 200 OK` with JSON body containing `"daemon_type":"authoritative"`. Any other response means the tunnel is down or the key is wrong — fix before running tofu.

### Rotating the API key

1. Generate a new key (e.g., `openssl rand -base64 24`)
2. Update `api-key=` in `/etc/powerdns/pdns.conf` on pantera
3. Restart: `systemctl restart pdns` on pantera
4. Update Ansible vault: `bootstrap/ansible/group_vars/all/vault.yml`
5. Update pass: `pass insert rbx/dns/pdns-api-key`
6. Validate with the curl command above

**DKIM records absent by design:** All four DKIM CNAME variables in `terraform.tfvars` are commented out. The Terraform resources use `count = var.dkim_X != "" ? 1 : 0`, so they are simply not created until values are provided. This is intentional — not a bug.

**.ch delegation complete:** rbxsystems.ch cutover propagated successfully. `dig @8.8.8.8 rbxsystems.ch NS +trace` resolves through ns1/ns2.rbxsystems.ch (pantera/eagle).

---

## 7. Secrets management (pass)

### Principle

`pass` is the single source of truth for all RBX operator secrets. Nothing secret lives in:
- `terraform.tfvars`
- committed files or env files
- shell history or ad-hoc exports

Ansible vault (`bootstrap/ansible/group_vars/all/vault.yml`) coexists with pass but serves a separate purpose: it stores secrets encrypted at rest in git, for use during `ansible-playbook` provisioning runs. When a secret exists in both stores, pass is the canonical reference — vault is updated from pass during key rotation.

### Directory layout

```
rbx/
  dns/
    pdns-api-key              # PowerDNS REST API key (pantera)
  db/
    pdns-password             # PostgreSQL password for pdns@jaguar
    robson-password           # PostgreSQL password for robson@jaguar
    robson-v2-password        # PostgreSQL password for robson_v2@jaguar
    truthmetal-password       # PostgreSQL password for truthmetal@jaguar
  email/
    postmark-api-token        # Postmark account API token (domain setup, server mgmt)
    postmark-smtp-password    # Postmark SMTP password (app outbound mail)
  storage/
    contabo-access-key        # Contabo object storage access key (S3-compatible)
    contabo-secret-key        # Contabo object storage secret key
  runtime/
    robson-api-key            # Robson service API key
    strategos-api-key         # Strategos service API key
```

### Naming conventions

| Suffix | Use case | Examples |
|--------|----------|---------|
| `-key` | API or access key (long-lived) | `pdns-api-key`, `contabo-access-key` |
| `-password` | Symmetric password (DB, SMTP) | `robson-password`, `postmark-smtp-password` |
| `-token` | Bearer token (may expire/rotate) | `postmark-api-token` |

Rules:
- kebab-case only — no underscores, no uppercase
- one secret per entry — no bundled credential files
- max three levels: `rbx/<group>/<name>`
- group by resource type, not by consumer
- no environment prefix for now (all prod); add `prod/` / `staging/` sub-group if environments split

### Basic usage

```bash
# Insert a new secret (interactive prompt — no echo)
pass insert rbx/dns/pdns-api-key

# Read a secret
pass rbx/dns/pdns-api-key

# List current tree
pass rbx/

# Generate a random 32-byte key and store it
pass generate rbx/db/robson-password 32
```

### Integration: OpenTofu (wrapper script)

The `pan-net/powerdns` provider reads `PDNS_API_KEY` and `PDNS_SERVER_URL` as provider-native env vars. All `tofu` runs go through the wrapper which injects these from pass:

```bash
# scripts/dns-tofu-env.sh
export PDNS_API_KEY="$(pass rbx/dns/pdns-api-key)"
export PDNS_SERVER_URL="http://127.0.0.1:18081"
exec "$@"
```

Usage:
```bash
~/apps/rbx-infra/scripts/dns-tofu-env.sh ~/.local/bin/tofu plan
~/apps/rbx-infra/scripts/dns-tofu-env.sh ~/.local/bin/tofu apply
```

For future OpenTofu modules that use TF_VAR_ variables (not provider-native env vars), inject from pass before the wrapper exec. Keep the secret out of `terraform.tfvars` — the TF_VAR_ approach only works when the variable has no value in any `.tfvars` file (tfvars takes precedence over TF_VAR_).

```bash
# Example for a future storage module
export TF_VAR_contabo_access_key="$(pass rbx/storage/contabo-access-key)"
export TF_VAR_contabo_secret_key="$(pass rbx/storage/contabo-secret-key)"
~/.local/bin/tofu plan
```

### Integration: Ansible

Ansible uses vault for provisioning. When running a playbook that needs a secret from pass (e.g., re-provisioning after key rotation), inject via environment before the playbook run:

```bash
# Re-provision pdns with a rotated API key
PDNS_API_KEY="$(pass rbx/dns/pdns-api-key)" \
  ansible-playbook -i inventory/hosts.yml site.yml --tags pdns
```

Inside the playbook, the variable would be sourced via `lookup('env', 'PDNS_API_KEY')`. Current playbooks use vault variables directly — this pattern applies when vault and pass are being kept in sync manually.

For the vault itself: after rotating a secret in pass, update vault with:
```bash
ansible-vault edit bootstrap/ansible/group_vars/all/vault.yml
```

### Integration: Kubernetes secrets

The `k8s-secrets` Ansible role (`bootstrap/ansible/roles/k8s-secrets/`) bootstraps all k8s secrets from pass. Run it whenever secrets need to be rotated or after cluster reinstall:

```bash
ansible-playbook ansible/site.yml \
  -i ansible/inventory/hosts.yml \
  --tags k8s-secrets
```

Prerequisites:
- `pass rbx/cluster/kubeconfig` populated (see below)
- All pass entries present (see pass directory layout in section 7)

The role:
1. Reads `rbx/cluster/kubeconfig` from pass → writes to `~/.kube/config-rbx` (0600)
2. Creates `robson/robsond-secret` (DATABASE_URL + PROJECTION_TENANT_ID)
3. Creates `robson/ghcr-pull-secret` (GHCR pull credentials)
4. Creates `monitoring/grafana-admin` (Grafana admin password)
5. Generates and stores `rbx/robson-v2/projection-tenant-id` in pass on first bootstrap

**Kubeconfig bootstrap** (one-time per cluster, after k3s is provisioned):

```bash
# Fetch from tiger and store in pass
ssh root@158.220.116.31 cat /etc/rancher/k3s/k3s.yaml \
  | sed 's|https://127.0.0.1:6443|https://158.220.116.31:6443|' \
  | pass insert --multiline rbx/cluster/kubeconfig
```

After this, `~/.kube/config-rbx` is derived from pass on every `k8s-secrets` role run — it is not tracked in Git or maintained separately.

**Pass keys used by k8s-secrets role:**

| pass key | k8s secret | field |
|----------|-----------|-------|
| `rbx/cluster/kubeconfig` | — (local file only) | kubeconfig |
| `rbx/cluster/ghcr-token` | `robson/ghcr-pull-secret` | `.dockerconfigjson` |
| `rbx/robson-v2/db-password` | `robson/robsond-secret` | `database-url` |
| `rbx/robson-v2/projection-tenant-id` | `robson/robsond-secret` | `projection-tenant-id` |
| `rbx/monitoring/grafana-admin-password` | `monitoring/grafana-admin` | `admin-password` |

For secrets that need to be rotated without re-running Ansible, use kubectl directly:

```bash
kubectl --kubeconfig ~/.kube/config-rbx create secret generic robsond-secret \
  --namespace robson \
  --from-literal=database-url="$(pass rbx/robson-v2/db-password | ...)" \
  --dry-run=client -o yaml | kubectl apply -f -
```

### What NOT to do

```bash
# WRONG — secret in shell history
export PDNS_API_KEY="secretvalue"

# WRONG — secret in tfvars
echo 'pdns_api_key = "secretvalue"' >> terraform.tfvars

# WRONG — secret in script file committed to git
API_KEY="secretvalue" ./run.sh
```

---

## 9. Robson v2 cluster state (as of 2026-04-11)

All MIG-v2.5 + MIG-v3#1 items are complete. MIG-v3#2 in progress:

| Item | State |
|------|-------|
| k3s cluster | tiger (control-plane) + altaica/sumatrae/jaguar (agents) |
| robsond | `ghcr.io/rbxrobotica/robson-v2:sha-e128478e`, namespace `robson` (moved from `robson-v2`) |
| ArgoCD `robson-prod` | Synced / Healthy (manages full stack) |
| ArgoCD `robson-v2-prod` | Archived — pending manual deletion |
| Migrations | 20240101000000–20240101000007 applied |
| PostgreSQL | ParadeDB on jaguar, pool connected via `DATABASE_URL` in secret |
| WebSocket | Reconnects on stream close (Binance closes periodically — normal) |
| k8s secrets | `robsond-secret`, `ghcr-pull-secret` in `robson`; `grafana-admin` in `monitoring` |

**Operational commands:**

```bash
# Check daemon status
kubectl --kubeconfig ~/.kube/config-rbx get pods -n robson
kubectl --kubeconfig ~/.kube/config-rbx logs -n robson deploy/robsond --tail=30

# Check migration state
kubectl --kubeconfig ~/.kube/config-rbx exec -n robson deploy/robsond -- robsond db status

# Re-bootstrap secrets (after key rotation or cluster reinstall)
ansible-playbook ansible/site.yml -i ansible/inventory/hosts.yml --tags k8s-secrets

# Force ArgoCD sync
kubectl --kubeconfig ~/.kube/config-rbx patch application -n argocd robson-prod \
  --type merge -p '{"operation":{"initiatedBy":{"username":"operator"},"sync":{"prune":true,"syncOptions":["CreateNamespace=true","RespectIgnoreDifferences=true"],"syncStrategy":{"hook":{}}}}}'
```

---

## 8. Next steps (DNS / email)

In this order:

1. ~~**Rotate pdns API key**~~ ✅ Done — key rotated and moved to `pass rbx/dns/pdns-api-key`
2. ~~**Bootstrap k8s secrets**~~ ✅ Done — `k8s-secrets` role deployed and tested (2026-04-10)
3. ~~**MIG-v3#1: Promote robsond as primary runtime**~~ ✅ Done (2026-04-10) — Django execution CronJobs suspended, robsond sole execution path
4. **Change strategos.gr NS** at .gr registrar to ns1/ns2.rbxsystems.ch
5. **Configure Postmark** — create Sender Signatures for:
   - `rbxsystems.ch`
   - `tx.rbxsystems.ch`
   - `strategos.gr`
   - `tx.strategos.gr`
6. **Add DKIM values** to `infra/terraform/dns/terraform.tfvars`
7. **`~/.local/bin/tofu apply`** (tunnel must be open: `ssh -f -N -L 127.0.0.1:18081:127.0.0.1:8081 root@149.102.139.33`)
8. **Validate email records:** SPF reachable, DKIM CNAME resolves, DMARC `p=none` in place
9. Verify in Postmark: domain status should show SPF + DKIM confirmed
