# INCIDENT 2026-04-25 — PowerDNS Crashloop on pantera

**Severity:** S2 — degraded (added DNS records blocked; existing
records still served from eagle's local cache and from public
recursive resolver caches).
**Detected:** 2026-04-25 during FE-P1 production launch, while
attempting `tofu apply` to add `robson.rbxsystems.ch`.
**Resolved:** same day, after correcting `gpgsql-password` in
`/etc/powerdns/pdns.conf` on pantera and restarting `pdns`.

---

## Timeline

- **Earlier (out of session):** the PostgreSQL password for the
  `pdns` database user (on jaguar, 161.97.147.76:5432) was
  rotated via Ansible. The vault file (`bootstrap/ansible/
  group_vars/all/vault.yml` or equivalent) was updated with the
  new value, but the rendered `/etc/powerdns/pdns.conf` on pantera
  was not redeployed. `pdns` continued running with the old
  config until its next restart.
- **Some time before this session:** pantera restarted (reason
  unknown — possibly a kernel update or VPS host event). On
  startup, `pdns` could not authenticate against the rotated
  PostgreSQL password and entered systemd restart loop. eagle (ns2)
  continued serving cached records via its bind backend, so most
  external resolution was unaffected.
- **2026-04-25, attempting to add `robson.rbxsystems.ch`:**
  `tofu plan` failed at the PowerDNS API call. The SSH tunnel on
  port 18081 was up but `pdns` was not listening on its API port
  because the daemon was crashlooping.
- **2026-04-25, ~17:00:** identified `pdns` restart counter at
  234922+. Reviewed `journalctl -u pdns` on pantera; saw repeated
  `gpgsql Connection failed: FATAL: password authentication
  failed for user "pdns"`.
- **2026-04-25, ~17:30:** retrieved the rotated password from
  Ansible vault, updated `/etc/powerdns/pdns.conf` on pantera
  atomically, restarted `pdns`. Service returned to `active
  (running)` immediately.
- **2026-04-25, ~17:40:** SSH tunnel re-established, `tofu apply`
  succeeded, `robson.rbxsystems.ch` A record created. Eagle picked
  up the zone via AXFR within seconds.
- **2026-04-25, ~17:50:** cert-manager emitted the Let's Encrypt
  cert for `robson.rbxsystems.ch` after the new record propagated
  to public resolvers; FE-P1 production launch completed.

---

## Root cause

Configuration drift between the vault password rotation and the
deployed `/etc/powerdns/pdns.conf`. Ansible managed both the
rotation and the rendered config, but the rendering step was
either skipped or never applied to pantera after the rotation.

`pdns` does not validate the database connection at boot in a way
that surfaces clearly: it starts, tries to query, fails, exits,
and systemd restarts it. The crashloop counter in
`systemctl status pdns` is the only obvious signal.

---

## Why it took as long as it did

- **Diagnosis surface was narrow.** From the workstation, the SSH
  tunnel appeared up (port listener present), but the API was
  unreachable. The default assumption was a tunnel problem, not a
  daemon problem.
- **External DNS still resolved.** Public recursive resolvers had
  cached most relevant records, and eagle continued to answer
  AXFR-replicated zones from its local bind backend. The only
  symptom was that *new* records could not be written.
- **No alerting on `pdns` health.** systemd restart-loop status
  was visible only by SSH-ing into pantera and checking
  `systemctl status pdns`.

---

## Fix

1. Located the correct password in the Ansible vault.
2. SSH'd to pantera, backed up the current config:
   ```bash
   cp /etc/powerdns/pdns.conf /etc/powerdns/pdns.conf.bak.$(date +%s)
   ```
3. Updated `gpgsql-password=` in `/etc/powerdns/pdns.conf` via a
   stdin-piped sed (so the password did not appear in `ps -ef`).
4. `systemctl restart pdns`; confirmed `active (running)`.
5. Verified API reachable: `curl -H "X-API-Key: ..."
   http://127.0.0.1:8081/api/v1/servers` returned `200`.

---

## Prevention (open follow-ups)

These are not done yet — captured here so we do not lose them.

1. **Ansible step that re-renders pdns.conf on every vault
   rotation.** Currently the vault edit is one playbook, the
   config render is another; nothing forces them in lockstep. The
   fix is a single playbook that rotates *and* re-renders *and*
   restarts `pdns` (with a guard).
2. **Health check for `pdns` exposed to monitoring.** A simple
   `systemctl is-active pdns` plus a probe against the API port
   from the workstation, run periodically, would have alerted
   immediately. Could be a Prometheus blackbox or a cron job.
3. **Document the password retrieval path.** Engineers should not
   have to discover that the password is in `vault.yml` by
   reading playbook source. See
   `docs/runbooks/DNS-TROUBLESHOOTING.md` for the canonical
   recovery procedure.
4. **Short MX/AXFR validation script.** After any DNS change to
   a zone, run a script that confirms eagle picked up the new
   serial. We did this manually here.

---

## What went right

- The `dns-tofu-env.sh` wrapper isolated the failure cleanly:
  `tofu plan` returned a clear "API unreachable" error rather than
  silently corrupting state.
- The `apps/archived/` pattern in rbx-infra meant the legacy
  Robson React frontend manifests had already been moved out of
  the active path; ArgoCD did not try to reconcile dead
  manifests on top of the DNS outage.
- Eagle's bind-backed slave continued serving existing zones, so
  no public-facing service went down — only new records were
  blocked.

---

## Related

- `docs/runbooks/DNS-TROUBLESHOOTING.md` — generalized recovery
  procedure derived from this incident.
- `docs/infra/DNS.md` — day-to-day DNS operations.
- `docs/infra/SECRETS.md` — `pass` store layout (operator
  workstation side).
- The `vault.yml` location and structure live in
  `bootstrap/ansible/` and require Ansible vault password access.
