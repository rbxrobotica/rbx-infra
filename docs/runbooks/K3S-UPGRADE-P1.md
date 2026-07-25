# Runbook - P1 k3s Supported-Version Upgrade

Status: **COMPLETE — 4 of 4 steps done (2026-07-22).** Cluster on `v1.36.2+k3s1`
(all 4 nodes), supported stable line. Roadmap finished; no further minor upgrades
until the next supported-line drift.

> **Resume urgency (security).** 1.34 is the OLDEST currently-supported Kubernetes
> minor: it enters maintenance **2026-08-27** (~6 weeks) and reaches **EOL
> 2026-10-27** (~3 months); the imminent 1.37 release drops it from support.
> Finishing to `v1.36.2+k3s1` (EOL 2027-04-28) restores full CVE coverage. The
> cluster already sat ~5 months on EOL `v1.32.3` (2026-02-28 → 2026-07-18) with
> zero upstream patches — do not stall again. Progress: `1.32.3` ✅→ `1.33.13` ✅→ `1.34.9` ✅→ `1.35.6` ✅→
> **`1.36.2`** (roadmap COMPLETE 2026-07-22). State record:
> `~/docs/k3s-ha-cluster-state.md` (final section).

This runbook upgrades the RBX k3s cluster from `v1.32.3+k3s1` back into a
supported Kubernetes release line. It is intentionally written as a controlled
procedure, not an instruction to run now.

## Current state

As of 2026-07-22:

- `tiger`, `altaica`, and `sumatrae` are k3s servers with embedded etcd.
- `jaguar` is an agent and database/analytics host.
- All nodes run **`v1.36.2+k3s1`** (upgraded from `v1.32.3` → `1.33.13` → `1.34.9`
  → `1.35.6` → `1.36.2` across the 2026-07-18/19/20/22 windows).
- Active upstream Kubernetes branches are `1.34` (oldest; EOL 2026-10-27),
  `1.35` (EOL 2026-12-28), and `1.36` (current stable; EOL 2027-04-28).

## Upgrade rule

Do not skip Kubernetes minor versions. The target sequence (concrete patches from
the k3s channel API, re-confirmed at each window):

1. ✅ DONE 2026-07-18 — `v1.32.3+k3s1` -> `v1.33.13+k3s1`.
2. ✅ DONE 2026-07-18/19 — `v1.33.13+k3s1` -> `v1.34.9+k3s1`.
3. ✅ DONE 2026-07-20 — `v1.34.9+k3s1` -> `v1.35.6+k3s1`.
4. ✅ DONE 2026-07-22 — `v1.35.6+k3s1` -> `v1.36.2+k3s1` (stable; roadmap complete).

Recheck K3s releases immediately before each window
(`https://update.k3s.io/v1-release/channels`). The exact patch version is
a maintenance-window decision, not a constant in this runbook.

## Pre-flight, read-only

```bash
export KUBECONFIG=~/.kube/config-rbx
kubectl get nodes -o wide
kubectl get --raw=/readyz?verbose
kubectl get applications.argoproj.io -n argocd
kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded -o wide
kubectl get pv,pvc -A -o wide
kubectl get apiservice v1beta1.metrics.k8s.io -o yaml
```

Record:

- current node versions;
- degraded ArgoCD apps;
- non-running pods;
- all PVCs and node-local `local-path` bindings;
- current etcd snapshot list and latest snapshot timestamp.

## Required gates before execution

- A human approves the exact maintenance window and target patch version.
- Latest etcd snapshot exists on the source servers.
- Snapshot sync to `jaguar` is current.
- At least one restore drill has been performed or explicitly waived.
- Public smoke-test URLs are listed.
- Rollback target for the current minor step is written down.
- ArgoCD degraded apps are classified as pre-existing or upgrade-induced.

## Execution shape

Upgrade one k3s server at a time. Preserve etcd quorum:

1. Upgrade a non-initial server.
2. Wait for node `Ready`.
3. Check etcd member health.
4. Check `/readyz?verbose`.
5. Repeat for the next server.
6. Upgrade `jaguar` agent after the servers are healthy.

Do not continue to the next minor version until all nodes are healthy on the
current target minor.

## Agent (jaguar) upgrade method — install-script gotcha

jaguar runs `k3s-agent.service` with the join `--server`/`--token` hardcoded in
the unit's `ExecStart` (no `K3S_URL` env, no `config.yaml`). **Do NOT** re-run
`curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=... sh -` on jaguar without
`K3S_URL`+`K3S_TOKEN`: with no `K3S_URL` the install script defaults to SERVER
mode, creates a bogus `k3s.service` that fails to start, and leaves stray
`/var/lib/rancher/k3s/server/` state. (Hit during the 1.33 window; recovered by
removing the bogus unit + restarting `k3s-agent.service`.)

Preferred in-place agent upgrade (no token handling) — replace the binary
directly, then restart the existing agent unit (preserves `--token`):

```bash
ssh jaguar 'bash -s' <<'EOF'
set -e
VER=v1.33.13+k3s1   # set per window from the channel API
sudo cp -a /etc/systemd/system/k3s-agent.service /etc/systemd/system/k3s-agent.service.pre-upgrade.bak
curl -sfL "https://github.com/k3s-io/k3s/releases/download/${VER/+/%2B}/k3s" -o /tmp/k3s.new
sudo install -m 0755 /tmp/k3s.new /usr/local/bin/k3s
sudo systemctl restart k3s-agent.service
EOF
# then: kubectl cordon/uncordon jaguar; wait Ready + new kubeletVersion
```

(Alternative: re-run the install script WITH `K3S_URL=https://158.220.116.31:6443`
and `K3S_TOKEN=<server token>`.) Always back up the agent unit first and verify
the join token survives (length check, never print the value).

## Post-step verification

After each node:

```bash
kubectl get nodes -o wide
kubectl get --raw=/readyz?verbose
kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded -o wide
kubectl get applications.argoproj.io -n argocd
```

After each minor:

- Verify all intended nodes are on the same K3s minor.
- Verify ArgoCD root and platform apps.
- Verify metrics-server, Traefik, CoreDNS, cert-manager, external-secrets,
  Prometheus, Loki, and Promtail.
- Run public HTTP smoke checks.
- Save a fresh etcd snapshot.

## Stateful / long-sync workloads (BTCPay) — do NOT fear sync loss

`rbx-payments` runs BTCPay on **altaica** (bitcoind StatefulSet, 60 Gi `local-path`
PVC) with its **Postgres external on jaguar** (Service `rbx-btcpay-postgres` →
manual endpoint `161.97.147.76:5432`). Observed across the 1.33 + 1.34 windows:

- **bitcoind blockchain sync is NOT lost on upgrade.** Its chain state (tip, UTXO
  set, block index) is persisted to the `bitcoind-data` PVC on altaica's disk; a
  restart resumes from that tip, it never re-syncs from zero. In both prior windows
  the bitcoind pod was not even restarted (`RESTARTS=0`) and the sync advanced
  uninterrupted (34.8% on 07-13 → 77.89% on 07-19 → 83.26% on 07-20 → 100% on 07-22). **The no-drain rule is what
  protects this** — never drain altaica, only cordon, so the pod is never evicted
  and its PVC never detaches.
- **After altaica restarts**, verify sync kept advancing (not stalled):
  `kubectl logs -n rbx-payments rbx-btcpay-bitcoind-0 --tail=1 | grep -oE 'height=[0-9]+ .*progress=[0-9.]+'`
  — sample twice ~15s apart; `height` must increase.
- **btcpay-server ↔ Postgres blip is expected and transient.** When jaguar
  restarts, btcpay-server's pooled Npgsql connections to `jaguar:5432` briefly
  break (Npgsql timeouts in `DelayedTransactionBroadcaster` around the window),
  then **self-recover** within minutes as the network path heals. Verify recovery:
  `kubectl run -n rbx-payments --rm -i --restart=Never --image=curlimages/curl:8.7.1 <name> -- curl -s -o /dev/null -w '%{http_code}' http://rbx-btcpay-server:23000/`
  → expect `302`. Only if it does NOT recover, `kubectl rollout restart deploy/rbx-btcpay-server -n rbx-payments` to clear the pool.

## Rollback posture

Rollback is per minor step. Do not attempt blind downgrade across multiple minor
versions. If rollback is required:

1. Stop at the first failing node or minor step.
2. Preserve logs and current etcd snapshot metadata.
3. Restore the last known-good snapshot only under explicit human authorization.
4. Reconcile GitOps after the control plane is stable.

## Known pre-existing issues not caused by upgrade

As of 2026-07-08:

- `metrics-server` unavailable.
- duplicate/default observability stack has pending node-exporters.
- `langfuse` degraded by `local-path` PVC mount failures.
- `truthmetal` CrashLoopBackOff and TLS challenge pending.
- `rbx-ledger-backend` CrashLoopBackOff.
- `rbx-cms` ImagePullBackOff for non-existent image tag.
- `rbx-console-users-access` ExternalSecret sync error.

Do not declare the upgrade failed solely because a pre-existing issue remains.
Do declare it failed if a healthy critical dependency regresses.
