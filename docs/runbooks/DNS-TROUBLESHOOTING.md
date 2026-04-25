# DNS Troubleshooting Runbook

**Audience:** engineer with SSH to pantera/eagle and Ansible vault
password.
**Out of scope:** designing new DNS records (that is in
`docs/infra/DNS.md`).

This runbook covers the common failure modes seen so far. The
2026-04-25 PDNS crashloop incident in `docs/incidents/` is the
canonical case study — read it first if you have time.

---

## Symptom matrix

| Symptom | Most likely cause | Jump to |
|---------|------------------|---------|
| `tofu apply` errors with "connection refused" or "API unreachable" | SSH tunnel down OR `pdns` not running on pantera | §1, §2 |
| `tofu apply` errors with "401 Unauthorized" | `PDNS_API_KEY` in `pass` is stale OR pantera config out of sync | §3 |
| `tofu apply` succeeds but `dig` shows old/no record | Tofu wrote to master but eagle hasn't picked up via AXFR yet | §5 |
| `pdns` stuck in `systemctl restart` loop on pantera | gpgsql password mismatch — config out of sync with vault rotation | §4 |
| Cert-manager challenge fails for a new host | DNS resolves at ns1 but not at public resolvers yet — wait for TTL | §6 |

---

## §1. Tunnel diagnosis

```bash
# Listener present?
ss -tnlp 2>/dev/null | grep 18081

# Tunnel process(es)?
pgrep -af "ssh.*-L.*18081"
```

If listener present but API does not respond: tunnel is up, the
problem is on the daemon side. Skip to §2.

If listener absent: recreate the tunnel.

```bash
pkill -f "ssh.*-L.*18081" 2>/dev/null; sleep 2
ssh -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -f -N \
    -L 127.0.0.1:18081:127.0.0.1:8081 \
    root@149.102.139.33
```

Verify:

```bash
PDNS_KEY=$(pass rbx/dns/pdns-api-key)
curl -s -m 10 -o /dev/null -w "%{http_code}\n" \
  -H "X-API-Key: $PDNS_KEY" \
  http://127.0.0.1:18081/api/v1/servers
unset PDNS_KEY
```

Expected: `200`. If `000` or timeout → §2. If `401` → §3.

---

## §2. `pdns` daemon health on pantera

```bash
ssh root@149.102.139.33 'systemctl is-active pdns'
```

If `active`: not your problem. Re-check tunnel/API key.

If `failed`, `activating`, or `inactive`:

```bash
ssh root@149.102.139.33 'journalctl -u pdns --no-pager -n 50'
```

Look for one of:

- `gpgsql Connection failed: FATAL: password authentication
  failed for user "pdns"` → §4 (password drift).
- `FATAL: database "pdns" does not exist` → contact operator;
  database integrity issue, not a config drift.
- `Address already in use` → another `pdns` process or stale
  pidfile; usually self-heals on `systemctl restart pdns`, but
  investigate before restarting blindly.

If you see the password-auth error, **stop and read §4 fully
before changing anything.**

---

## §3. API key (`PDNS_API_KEY`) drift

The API key lives in two places:

- `pass` store: `pass show rbx/dns/pdns-api-key`
- pantera config: `/etc/powerdns/pdns.conf` line `api-key=...`

If the `dns-tofu-env.sh` wrapper sees `401`, the workstation key
disagrees with pantera. Source of truth is **pantera's config**
(rendered by Ansible). Sync `pass` to match.

```bash
# Check what pantera has (operator-only; do not paste in chat):
ssh root@149.102.139.33 'grep ^api-key= /etc/powerdns/pdns.conf'
```

If you must rotate the API key, do it via Ansible (vault edit +
playbook re-render + `systemctl restart pdns`), not by editing
`/etc/powerdns/pdns.conf` directly. Then update `pass` to match.

---

## §4. gpgsql password drift — the 2026-04-25 incident

Symptom: `pdns` crashloops with
`FATAL: password authentication failed for user "pdns"`.

**Root cause:** the PostgreSQL password for the `pdns` database
user was rotated in Ansible vault (`bootstrap/ansible/
group_vars/all/vault.yml` or equivalent), but pantera's rendered
`/etc/powerdns/pdns.conf` was not updated.

### Recovery

1. **Read the vault** to retrieve the current password. Ansible
   vault password is operator-issued.
   ```bash
   cd ~/apps/rbx-infra/bootstrap/ansible
   ansible-vault view group_vars/all/vault.yml
   # find pdns_db_password (or whatever the variable is named)
   ```

2. **Confirm the password works** from the Postgres side. The
   safest shape is to pipe the password to a remote `psql` so it
   never appears in shell history:
   ```bash
   ssh root@149.102.139.33 \
     "PGPASSWORD=\"\$(cat)\" psql \
        -h 161.97.147.76 -p 5432 -U pdns -d pdns -c 'SELECT 1' 2>&1" \
     < <(ansible-vault view ~/apps/rbx-infra/bootstrap/ansible/group_vars/all/vault.yml \
         | yq '.pdns_db_password')
   ```
   Expected: `?column?` row with value `1`. If FATAL: password
   auth failed → vault password is also stale, contact the
   operator. Do not proceed.

3. **Backup pantera config:**
   ```bash
   ssh root@149.102.139.33 'cp /etc/powerdns/pdns.conf \
     /etc/powerdns/pdns.conf.bak.$(date +%s)'
   ```

4. **Update `gpgsql-password=` atomically** without ever printing
   the password in shell history:
   ```bash
   ansible-vault view ~/apps/rbx-infra/bootstrap/ansible/group_vars/all/vault.yml \
     | yq '.pdns_db_password' \
     | ssh root@149.102.139.33 'NEW=$(cat); \
         sed -i "s|^gpgsql-password=.*|gpgsql-password=$NEW|" /etc/powerdns/pdns.conf; \
         grep "^gpgsql-password=" /etc/powerdns/pdns.conf | sed "s|=.*|=<redacted>|"'
   ```
   The final `grep | sed` confirms the line is now present without
   leaking the value.

5. **Restart `pdns`:**
   ```bash
   ssh root@149.102.139.33 'systemctl restart pdns; sleep 3; \
     systemctl is-active pdns; \
     journalctl -u pdns --no-pager -n 10 | tail'
   ```
   Expected: `active`. Logs should show normal startup, no auth
   errors.

6. **Reverify API:** repeat §1 verification block. Should now
   return `200`.

7. **Open a follow-up issue** to add an Ansible playbook step
   that re-renders `pdns.conf` and restarts `pdns` automatically
   on every vault password rotation. This is the canonical
   prevention.

---

## §5. AXFR replication lag (eagle out of sync)

After a successful `tofu apply` against pantera, eagle should
pick up the new zone serial within seconds via NOTIFY+AXFR.

Verify directly at each authoritative server:

```bash
dig +short A robson.rbxsystems.ch @149.102.139.33   # ns1 (pantera)
dig +short A robson.rbxsystems.ch @167.86.92.97     # ns2 (eagle)
```

If pantera answers but eagle does not after ~30s:

```bash
ssh root@149.102.139.33 'pdns_control notify rbxsystems.ch'
```

This forces a NOTIFY to slaves. eagle's bind backend should then
trigger AXFR.

If eagle still doesn't pick up:

```bash
ssh root@167.86.92.97 'rndc retransfer rbxsystems.ch || \
  systemctl status named --no-pager | head -20'
```

---

## §6. Cert-manager challenge stuck after DNS change

After a new A record is created and replicated, cert-manager
attempts an HTTP-01 challenge. If it fails, common causes:

- **Public resolver cache.** External resolvers may not see the
  new record for up to TTL seconds (default 3600). cert-manager
  retries automatically; usually clears within 5–15 minutes.
- **Negative cache.** If the host was queried *before* the record
  existed, recursive resolvers may cache an NXDOMAIN. Public
  resolvers respect TTL on negative answers (typically 300s).
  Wait it out.
- **Ingress not routing `/.well-known/acme-challenge/*`.** Check
  the Ingress for the host has a path rule that allows the
  challenge path through to cert-manager's HTTP solver.

See `docs/runbooks/CERT-MANAGER-DEBUG.md` for full procedure.

---

## What NOT to do

- **Do not edit `/etc/powerdns/pdns.conf` for permanent config
  changes.** Edit the Ansible template and re-render. The config
  on the host should be reproducible.
- **Do not commit `pdns.conf.bak.*` to git.** Backups stay on the
  host.
- **Do not export `PDNS_API_KEY` in your shell history.** The
  `dns-tofu-env.sh` wrapper handles this; respect the boundary.
- **Do not restart `pdns` on pantera while a `tofu apply` is in
  progress.** Wait for the apply to finish or fail.
- **Do not bypass eagle.** Even if eagle is lagging, do not
  configure clients to use only ns1. The two-server design is
  non-negotiable for resilience.
