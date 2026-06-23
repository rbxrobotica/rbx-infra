# INCIDENT 2026-06-22 — k3s kine/SQLite Datastore Bloat → Control-Plane Crash-Loop

**Severity:** S1 — full public ingress outage (all sites returning `000`).
**Detected:** 2026-06-22, reported by operator ("houve um incidente no cluster"); kubelet
on the single control-plane node `tiger` stopped posting status at **03:40 UTC**.
**Resolved:** 2026-06-22 ~07:11 UTC, after compacting the kine datastore (17 GB → 252 MB)
and restarting k3s. All public services back to 200/302.

---

## Impact

- `robson.rbx.ia.br`, `merovelis.com`, `rbx.ia.br`, `rbxsystems.ch`, `auth.merovelis.com` — all unreachable (`000`) for the duration.
- Root cause was control-plane wide; data plane (pods) largely kept running but were removed from Service endpoints when the node went `NotReady`.

## Timeline (UTC)

- **03:40** — kubelet on `tiger` (sole k3s server) stops posting node status. k3s server process is crash-looping.
- **03:42** — node `tiger` transitions to `NotReady` (`NodeStatusUnknown`).
- **05:49** — `node.kubernetes.io/unreachable:NoExecute` taint applied → eviction churn begins; Traefik/CoreDNS/ArgoCD endpoints emptied → ingress dead.
- **morning** — operator reports incident. Triage finds: host alive (ping/SSH/6443 OK), load avg ~30 on a 4-core box, no OOM, disk 76% — but k3s in `activating` (restart loop).
- Root cause identified: k3s journal shows `cloud-controller-manager panic: ... [-]etcd failed ... healthz check failed`; datastore `state.db` (kine/SQLite) is **17 GB**.
- **~06:39** — `systemctl stop k3s`; WAL checkpointed; verified byte-identical backup taken.
- **~07:10** — kine compacted via table rebuild (keep latest revision per key) → 252 MB; swapped in.
- **~07:11** — `systemctl start k3s`; control plane stable, no further panics.
- **~07:15** — `tiger` `Ready`; endpoints repopulated; Traefik pod rescheduled to `altaica`; sites return 200.

## Root Cause

k3s ran on its **embedded SQLite datastore** (kine, file `state.db`). kine compaction of old
object revisions had **stalled at revision 7,225,554** while writes continued to revision
9,744,664 — leaving ~2.5M uncompacted (orphan) revisions and bloating `state.db` to **17 GB**
(0 free pages — genuine data, not free-page bloat; `VACUUM` alone would not have helped).

A 17 GB SQLite datastore is too slow for the apiserver storage health check (`vmstat` showed
`wa 17`, ~40 MB/s random reads). On each k3s start, the storage `healthz` timed out →
`cloud-controller-manager` panicked → k3s process exited → systemd restarted it → **crash-loop**.
Single control-plane node ⇒ no redundancy ⇒ full outage.

Likely a vicious cycle: as the DB grew, compaction transactions could not complete against the
slow/over-loaded datastore, so the compaction marker never advanced, so the DB kept growing.
Chronic crash-looping pods (`rbx-data` 5867, `lda-prod` 1319, `argocd-image-updater` 1200
restarts) amplified write churn (events/leases/status).

## Resolution

1. `systemctl stop k3s` on `tiger` (stopped the I/O storm, released the DB lock); `PRAGMA wal_checkpoint(TRUNCATE)`.
2. **Verified backup** of the quiesced, consistent `state.db` (byte-identical to source; header/size/indexed-query checked) → `state.db.pre-compact-20260622T064118Z`.
3. Compaction via **table rebuild** (the in-place `DELETE … VACUUM` was aborted because journal_mode would not switch to OFF and the WAL grew toward filling the disk):
   - `ATTACH` source, `CREATE TABLE kine (…)`, `INSERT … SELECT * FROM src.kine WHERE id IN (SELECT MAX(id) FROM src.kine GROUP BY name)` (keep latest revision per key), recreate indexes, bump `compact_rev_key`.
   - Result: **2,521,327 → 49,779 rows**, **17 GB → 252 MB**, `quick_check ok`, AUTOINCREMENT high-water (`9,745,677`) preserved so revisions continue monotonically.
4. Swap new DB in, `systemctl start k3s`. Storage `healthz` passed quickly; no further panic.

## Contributing Factors / Systemic Gaps Found

- **Single control-plane node** (no HA) — datastore problem became a full outage.
- **No automated backup** of cluster state. The ad-hoc copy made to `jaguar` during the incident
  was a **truncated/corrupt hot-copy** (3.4 GB of an expected 17.8 GB; `quick_check` failed on
  high pages) and **would not have restored**.
- **No working monitoring/alerting**: `kube-prometheus-stack`/`loki`/`promtail` exist as ArgoCD
  apps but are `Unknown`/not applied; `metrics-server` is down. The incident ran for hours unseen.
- **Secrets encryption at rest disabled** (plaintext in the datastore/backup).
- Critical singletons (Traefik, CoreDNS, ArgoCD, ESO, cert-manager) all `replicas=1`, co-located on the master.

## Action Items

Tracked in the resilience/security plan (see `docs/runbooks/K3S-HA-MIGRATION.md` and the
session plan). Priority order set by operator:

1. **[P1] Control-plane HA** — migrate to embedded-etcd with 3 servers (`tiger`+`altaica`+`sumatrae`); native etcd snapshots; fold in control-plane hardening (secrets-encryption, audit log, `write-kubeconfig-mode 0600`, etcd-snapshot offsite). Scheduled window: **2026-06-23**.
2. **[P2] Observability & alerting** — bring up the scaffolded Prometheus/Loki stack + metrics-server; alerts for node `NotReady`, k3s crash-loop, **datastore size / compaction gap**, empty critical endpoints, cert expiry.
3. **[P2] Automated, verified backups** — etcd snapshots + offsite (rsync.net Zurich, ADR-0004) + documented restore drill. Never rely on `cp` of a live SQLite file.
4. **[P3] Hygiene** — fix/zero chronic crash-loopers (kine churn sources); clean stale jobs/namespaces.
5. **[P3] Broader security** — default-deny NetworkPolicies, PodSecurity admission, RBAC review.

## Lessons

- A `cp` of a live SQLite/kine file is **not** a valid backup; use `.backup`, or stop+checkpoint+copy+`integrity_check`.
- Embedded SQLite kine is fragile at this scale; etcd (HA, native snapshots) or external Postgres is the resilient path.
- Free-page vs genuine-data matters: check `PRAGMA freelist_count` before assuming `VACUUM` will shrink a DB.

## Artifacts (on `tiger`, `/var/lib/rancher/k3s/server/db/`)

- `state.db` — compacted live datastore (~252 MB).
- `state.db.pre-compact-20260622T064118Z` — verified 17 GB pre-compaction backup (rollback).
- `state.db.bloated-old` — pre-compaction live copy (redundant; safe to remove after HA migration).
