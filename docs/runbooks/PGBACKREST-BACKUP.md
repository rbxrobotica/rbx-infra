# Runbook: pgBackRest backup for the jaguar cluster (ADR-0012)

Whole-cluster backup (ZITADEL, PowerDNS, Robson, Langfuse, warehouse,
commerce) with continuous WAL archiving. Local repo first; off-site
repo (rsync.net Zurich) is phase 4. The single disruptive step in the
whole rollout is one PostgreSQL restart in phase 2.

Safety property of the deploy order: the role creates the stanza
BEFORE placing the archiving drop-in, so an unscheduled restart at any
point activates a WORKING archiver. WAL archived before the scheduled
window simply accumulates (bounded, compressed) in the local repo
until phase 3's first backup gives retention something to expire.

## Phase 1: deploy (no impact on the running cluster)

Once, on the operator machine:

```
pass generate rbx/backup/pgbackrest-cipher 64
```

Then record it in the audit: the passphrase becomes the Root of
Recovery's backup-encryption item (replacing or complementing the
placeholder `age-backup`; update the component's `encryption_key_ref`
accordingly). Two locations, two custodians apply to it like any
material item. Then:

```
cd bootstrap/ansible
ansible-playbook pgbackrest.yml -i inventory/hosts.yml
```

This installs and holds the package, writes the config, creates the
stanza and places the archiving drop-in. It ends by reporting that a
restart is pending. Check disk headroom before proceeding: at peak
(expiry runs after a new full completes) the local repo holds about 3
compressed fulls plus up to two weeks of WAL, on the same filesystem
as the data directory and the etcd snapshot copies:

```
ssh jaguar 'df -h /var/lib && sudo -u postgres psql -tAc "SELECT pg_size_pretty(sum(pg_database_size(datname))) FROM pg_database"'
```

## Phase 2: the restart window (minutes, scheduled by a human)

Impact: ZITADEL logins and PDNS zone writes fail for the duration of
the restart (seconds to a minute); DNS keeps answering from the
running nameservers. Pick a quiet window, and schedule it soon after
phase 1: while the restart is pending, other plays that reload
Postgres will arm `pending_restart`, and the robson bronze
provisioning preflight refuses to run with a pending restart (that
block is by design; the fix is doing this restart).

```
ssh jaguar 'sudo systemctl restart postgresql'
ssh jaguar 'sudo -u postgres psql -tAc "SHOW archive_mode"'   # must print: on
```

Emergency stop if archiving misbehaves after the restart: set
`archive_command = '/bin/true'` in the drop-in and reload (no second
restart). WAL recycled while `/bin/true` is active is permanently
missing from the archive: after restoring the real command, take a new
full backup to re-anchor point-in-time recovery.

## Phase 3: verify, first backup, timers

```
cd bootstrap/ansible
ansible-playbook pgbackrest.yml -i inventory/hosts.yml
ssh jaguar 'sudo -u postgres pgbackrest --stanza=jaguar --repo=1 --type=full backup'
ssh jaguar 'sudo -u postgres pgbackrest info'
```

The re-run executes `check` (now that archiving is live) and enables
the timers. `pgbackrest info` must show the full backup and a WAL
archive range. From here on: full on Sunday 03:00, differential Monday
to Saturday 03:00, WAL continuously (archive_timeout 60s). Timers are
non-persistent on purpose: a missed run waits for the next slot
instead of racing crash recovery at boot; WAL archiving carries the
RPO in between.

## Phase 4: off-site to rsync.net Zurich (when the account exists)

1. In a host_vars/group_vars PR, set `pgbackrest_offsite_enabled: true`
   and fill `pgbackrest_offsite_host`, `pgbackrest_offsite_host_user`
   and `pgbackrest_offsite_repo_path` from the account welcome e-mail.
2. Pin the host key: `pgbackrest_offsite_host_fingerprint` takes the
   **lowercase hex sha256 of the host key**, not the `SHA256:base64`
   form ssh-keygen prints. Derive it from a trusted network:

   ```
   ssh-keyscan -t ed25519 <host> 2>/dev/null | awk '{print $3}' | base64 -d | sha256sum
   ```

3. Re-run the playbook. It generates jaguar's dedicated ed25519 key
   and prints the public key; register that key at rsync.net,
   restricted to its own subdirectory. The re-run also re-executes
   stanza-create, which now initializes the stanza on repo2 as well
   (it is idempotent on repo1), and installs the repo2 timers
   (pgBackRest backs up one repository per invocation, so repo2 has
   its own schedule one hour after repo1's).
4. Verify both repos and seed repo2 with its first full:

   ```
   ssh jaguar 'sudo -u postgres pgbackrest --stanza=jaguar check'
   ssh jaguar 'sudo -u postgres pgbackrest --stanza=jaguar --repo=2 --type=full backup'
   ssh jaguar 'sudo -u postgres pgbackrest info'
   ```

5. rsync.net side: confirm ZFS snapshots are on (7 daily); they are
   the deletion/ransomware backstop outside jaguar's credentials.

## Phase 5: restore drill (audit evidence)

Restore to bengal, never to production, per the rbx-resilience drill
`restore-postgres-zitadel`. Sketch: install postgres 16 + pgbackrest on
bengal, copy a restore-only config (repo read access, cipher pass from
pass), run `pgbackrest --stanza=jaguar restore` to an empty data dir,
start postgres on a private port, run the verification query against
the restored zitadel database, record rto_observed/rpo_observed and
findings, wipe the target. Evidence (log + report, secrets redacted)
goes to the audit repository, not here.

## Known interactions

- **paradedb tuning handler**: a change to rbx-postgresql-tuning.conf
  in a site.yml run restarts Postgres via its handler. After phase 1
  that restart activates archiving early; harmless by design (stanza
  exists), but it consumes the phase 2 window unscheduled. Prefer
  doing phase 2 deliberately before any paradedb tuning change.
- **postgresql-16 package upgrades** (unattended or manual) restart
  the cluster via maintainer scripts; same consequence, same safety.
- **robson bronze provisioning preflight** hard-fails while a restart
  is pending (see phase 2). Not a malfunction.

## Standing rules

- The passphrase never appears in git, inventory or command lines on
  shared hosts; it lives in pass and in the postgres-owned 0600 config
  on jaguar.
- Backup failure visibility: `systemctl list-timers "pgbackrest-*"`
  and `/var/log/pgbackrest/` until proper alerting lands (deliberate
  gap; candidate for the observability stack, not for cron hacks).
- Package upgrades: unhold, upgrade, re-hold, in a PR-documented step.
- Retention or schedule changes are PRs against the role defaults.
