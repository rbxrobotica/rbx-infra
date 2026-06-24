# Runbook — k3s Single-Server → Embedded-etcd HA Migration (+ Control-Plane Hardening)

> **STATUS: COMPLETED 2026-06-24** — migration executed successfully; Ansible codified in PR #59.
> See [Execution Notes](#execution-notes-2026-06-24) below for actual outcomes and corrections.

**Audience:** RBX operators executing the control-plane HA migration.
**Prerequisites:** root SSH to all nodes (key-based); `KUBECONFIG=~/.kube/config-rbx`; ArgoCD admin; `pass` access; an offsite backup target (rsync.net Zurich, ADR-0004).
**Origin:** Action item P1 from `docs/incidents/INCIDENT-2026-06-22-KINE-BLOAT.md`.

> **Warning — this is a destructive, in-place control-plane rebuild.** k3s cannot convert an
> existing SQLite datastore to embedded etcd in place; the datastore is re-initialized. Do this
> only inside a planned maintenance window, with the full capture (Step 1) completed and the
> rollback (Step 7) tested. Blue/green (a parallel etcd cluster, cut over by DNS) would be safer
> but needs extra nodes; the 4 VPS are all in use, so this is an in-place rebuild.

## Target topology

| Node | IP | Role after migration |
|---|---|---|
| tiger | 158.220.116.31 | server (etcd, `--cluster-init`) |
| altaica | 173.212.246.8 | server (etcd, join) |
| sumatrae | 5.189.178.212 | server (etcd, join) |
| jaguar | 161.97.147.76 | agent (db/analytics, tainted) — unchanged |

etcd quorum = 3 ⇒ tolerates loss of 1 server. Current k3s: `v1.32.3+k3s1` (pin the same version).

---

## Step 0 — Pre-flight (read-only)

```bash
export KUBECONFIG=~/.kube/config-rbx
kubectl get nodes -o wide
kubectl get applications -n argocd            # note OutOfSync: llm-gateway, rbx-data, rbx-memory, rbx-observability
df -h / ; ssh root@158.220.116.31 'df -h /'   # ensure space for capture
```

Confirm a maintenance window and announce downtime (control plane + ingress will blip).

## Step 1 — Full capture (safety net; non-destructive — can be pre-staged the day before)

1. **All cluster resources** (incl. Secrets/ConfigMaps/CRDs) — Velero, or raw export:
   ```bash
   mkdir -p ~/k3s-migration-$(date -u +%Y%m%d)/{resources,tls,pvc}
   for ns in $(kubectl get ns -o name | cut -d/ -f2); do
     kubectl get -n "$ns" all,cm,secret,ingress,pvc,sa,role,rolebinding -o yaml > ~/k3s-migration-*/resources/$ns.yaml 2>/dev/null
   done
   kubectl get crd,clusterrole,clusterrolebinding,clusterissuer,pv,validatingwebhookconfiguration,mutatingwebhookconfiguration -o yaml > ~/k3s-migration-*/resources/_cluster.yaml
   ```
2. **TLS secrets** (preserve to avoid 29× Let's Encrypt re-issue / rate-limit):
   ```bash
   kubectl get secret -A --field-selector type=kubernetes.io/tls -o yaml > ~/k3s-migration-*/tls/all-tls.yaml
   ```
3. **local-path PVC data** (~82 GiB Langfuse: ClickHouse 50Gi, Zookeeper 3×8Gi, Valkey 8Gi). Find each PVC's node + path and rsync the directory:
   ```bash
   kubectl get pvc -A -o jsonpath='{range .items[*]}{.metadata.namespace}{" "}{.spec.volumeName}{"\n"}{end}'
   # on the owning node: /var/lib/rancher/k3s/storage/<pvc-...>/  → rsync to offsite
   ```
4. **Secret audit** — diff the 124 cluster Secrets vs the 22 ExternalSecrets; ensure anything not
   reconstructible from `pass`/ESO is captured above (it is, via Step 1.1/1.2).
5. **etcd-able snapshot of current state** is N/A (source is SQLite) — the verified
   `state.db.pre-compact-*` on `tiger` is the single-server rollback image.

## Step 2 — Quiesce + final snapshot

```bash
ssh root@158.220.116.31 'systemctl stop k3s; sqlite3 /var/lib/rancher/k3s/server/db/state.db "PRAGMA wal_checkpoint(TRUNCATE);"; cp -a /var/lib/rancher/k3s/server/db/state.db /var/lib/rancher/k3s/server/db/state.db.window-$(date -u +%Y%m%dT%H%M%SZ)'
```

## Step 3 — Re-init `tiger` with embedded etcd + hardening

Write `/etc/rancher/k3s/config.yaml` on `tiger` (codify into `roles/k3s-server`; see Step 8), then re-init:

```yaml
# /etc/rancher/k3s/config.yaml  (tiger — first server)
cluster-init: true
tls-san:
  - 158.220.116.31
node-name: tiger
write-kubeconfig-mode: "0600"        # was 644
secrets-encryption: true             # encryption-at-rest
# audit log
kube-apiserver-arg:
  - "audit-log-path=/var/lib/rancher/k3s/server/logs/audit.log"
  - "audit-policy-file=/etc/rancher/k3s/audit-policy.yaml"
  - "audit-log-maxage=30"
  - "enable-admission-plugins=NodeRestriction"
# native etcd snapshots + offsite
etcd-snapshot-schedule-cron: "0 */6 * * *"
etcd-snapshot-retention: 20
# etcd-s3 + etcd-s3-* …  (point at offsite/S3-compatible target)
# evaluate after a clean bring-up: protect-kernel-defaults: true ; profile: cis
```

> A fresh `--cluster-init` ignores the old SQLite `state.db` (etcd is a different datastore). The
> cluster comes up **empty** — Step 4 restores content.

Re-init (same version):
```bash
ssh root@158.220.116.31 'curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="v1.32.3+k3s1" sh -s - server'
ssh root@158.220.116.31 'k3s etcd-snapshot save --name pre-restore || true; kubectl get nodes'
ssh root@158.220.116.31 'k3s secrets-encrypt status'   # expect: Enabled
```

## Step 4 — Restore cluster content

```bash
export KUBECONFIG=~/.kube/config-rbx   # refresh from tiger (mode 0600 now)
kubectl apply -f ~/k3s-migration-*/resources/_cluster.yaml      # CRDs/cluster-scoped first
kubectl apply -f ~/k3s-migration-*/tls/all-tls.yaml             # TLS secrets (skip LE re-issue)
for f in ~/k3s-migration-*/resources/*.yaml; do kubectl apply -f "$f"; done
# restore local-path PVC data to the owning nodes' /var/lib/rancher/k3s/storage/ before the consumers start
```

## Step 5 — Join altaica + sumatrae as servers

`/etc/rancher/k3s/config.yaml` on each (same hardening flags, plus):
```yaml
server: https://158.220.116.31:6443
token: <from tiger:/var/lib/rancher/k3s/server/node-token>
node-name: altaica   # / sumatrae
```
```bash
for h in 173.212.246.8 5.189.178.212; do ssh root@$h 'curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="v1.32.3+k3s1" sh -s - server'; done
ssh root@158.220.116.31 'kubectl get nodes; k3s kubectl -n kube-system get pods -l component=etcd'
```

## Step 6 — Reconcile, de-SPOF, verify

1. ArgoCD reconciles drift (app-of-apps `root`). Resolve pre-existing OutOfSync apps.
2. **Spread singletons** off the master: give Traefik & CoreDNS `replicas>=2` + `topologySpreadConstraints` (edit `platform/coredns/` and the Traefik HelmChartConfig).
3. Verification:
   ```bash
   kubectl get nodes                      # 3 control-plane Ready + jaguar agent
   ssh root@158.220.116.31 'k3s etcd-snapshot ls'   # snapshots present
   # failure test: stop k3s on ONE server, confirm cluster still serves (quorum 2/3) and sites stay 200
   for u in https://robson.rbx.ia.br https://merovelis.com https://rbx.ia.br https://rbxsystems.ch https://auth.merovelis.com; do curl -s -o /dev/null -w "%{http_code} $u\n" --max-time 10 "$u"; done
   ```

## Step 7 — Rollback (if etcd does not stabilize)

The window's downtime already exists, so rolling back is low marginal cost:
```bash
# Reinstall tiger as single-server SQLite and restore today's known-good datastore
ssh root@158.220.116.31 '/usr/local/bin/k3s-uninstall.sh'
ssh root@158.220.116.31 'curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="v1.32.3+k3s1" sh -s - server --tls-san 158.220.116.31 --node-name tiger --write-kubeconfig-mode 644'
ssh root@158.220.116.31 'systemctl stop k3s; cp -a /var/lib/rancher/k3s/server/db/state.db.pre-compact-20260622T064118Z /var/lib/rancher/k3s/server/db/state.db; rm -f /var/lib/rancher/k3s/server/db/state.db-wal /var/lib/rancher/k3s/server/db/state.db-shm; systemctl start k3s'
# re-join altaica/sumatrae/jaguar as agents (roles/k3s-agent)
```

## Step 8 — Codify (after a successful migration)

✅ **Done in PR #59 (2026-06-24).**

- `bootstrap/ansible/inventory/hosts.yml` — altaica+sumatrae in `k3s_server`; tiger has `k3s_cluster_init: true`.
- `bootstrap/ansible/roles/k3s-server/templates/k3s-config.yaml.j2` — renders `cluster-init: true` for tiger and `server:` + token for joining servers; includes all hardening.
- `bootstrap/ansible/roles/k3s-server/files/audit-policy.yaml` — static audit policy.
- `bootstrap/ansible/roles/k3s-server/handlers/main.yml` — restart k3s on config change.
- `bootstrap/ansible/site.yml` — `serial: 1` on k3s_server play (tiger-first; token in hostvars before join renders template).

## Notes

- ~~Consider a **dry-run** of Steps 3–4 on a throwaway VPS before the prod window.~~
- Keep `state.db.pre-compact-20260622T064118Z` until HA is proven over several days (then safe to remove).
- Remove `state.db.bloated-old` on `tiger` now (~17 GB; confirmed redundant post-migration).

---

## Execution Notes (2026-06-24)

### What actually happened — step-by-step corrections

**Step 3 (tiger re-init):** k3s with `cluster-init: true` did NOT start with an empty cluster as
the runbook warned. It **transparently migrated** the compacted `state.db` (252 MB) to embedded
etcd. All objects, secrets, ArgoCD apps, and PVCs were intact immediately after restart.

**Step 4 (restore) was SKIPPED entirely** — content was already present from the migration.
ArgoCD reconciled within minutes from git. All sites returned 200 without any `kubectl apply`.

**Step 5 — joining altaica + sumatrae (two corrections):**

1. **Write config.yaml AFTER the uninstall.** `k3s-server-uninstall.sh` deletes `/etc/rancher/k3s/`
   including any pre-written config.yaml. If you write the config first and then uninstall, the
   config is gone. Order: uninstall → write config.yaml → install/start.

2. **Wipe stale server state before joining.** After a brief incorrect standalone start, altaica and
   sumatrae had `/var/lib/rancher/k3s/server/` with their own bootstrap etcd state.
   k3s refused to join with "critical configuration value mismatch between servers."
   Fix: `rm -rf /var/lib/rancher/k3s/server/` on both nodes before rewriting config and restarting.

3. **`secrets-encryption: true` must be on ALL server nodes.** Joining servers without this flag
   failed to decrypt the existing AES-CBC secrets: "identity transformer tried to read encrypted
   data; reinitializing..." The Ansible template now unconditionally sets this on all server nodes.

### Result

```
NAME       STATUS   ROLES                       AGE
tiger      Ready    control-plane,etcd,master   live
altaica    Ready    control-plane,etcd,master   joined
sumatrae   Ready    control-plane,etcd,master   joined
jaguar     Ready    agent                        unchanged
```

etcd at 150 MB. state.db absent on all nodes. Secrets encryption enabled. Audit log active.
etcd snapshots every 6h. All public sites 200. Zero services required manual restoration.
