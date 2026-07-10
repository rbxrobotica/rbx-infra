# Runbook - Langfuse local-path PVC recovery

Status: operational runbook, human-gated. This document authorizes no mutation by
itself.

Audience: RBX infra operators recovering Langfuse when ClickHouse or ZooKeeper
PVCs are backed by `local-path` and kubelet reports missing host directories.

## Safety boundary

Do not delete PVCs, StatefulSets, pods, PVs, or local-path directories as a first
step. Do not run `kubectl apply`, `kubectl delete`, `kubectl patch`, `argocd app
sync`, node restarts, recursive `chmod`, or recursive `chown` without explicit
operator approval in the active incident.

Langfuse ClickHouse and ZooKeeper state can be either durable telemetry state or
disposable incident state depending on the business decision. The operator must
classify the data before repair.

## Known incident - 2026-07-09

After the k3s HA migration, Langfuse had missing `local-path` directories for:

- `data-langfuse-clickhouse-shard0-0` on `altaica`.
- `data-langfuse-zookeeper-1` on `sumatrae`.
- `data-langfuse-zookeeper-2` on `altaica`.

No matching historical directories were found on `tiger`, `altaica`, `sumatrae`,
or `jaguar`. The operator approved disposable recovery, so the missing
directories were recreated empty with local-path-compatible ownership and mode.
ClickHouse and ZooKeeper recovered. `langfuse-web` then needed a separate probe
window fix, merged in PR #99.

## Read-only diagnosis

Use the read-only audit script first:

```bash
scripts/audit-local-path-pv-dirs.sh langfuse
```

In the RBX operator environment, use the approved wrappers:

```bash
KUBECTL='rtk kubectl' SSH_CMD='rtk ssh' scripts/audit-local-path-pv-dirs.sh langfuse
```

Expected output shape:

```text
OK      <pv>  langfuse/<pvc>  <size>  <node>  <mode owner:group path>
MISSING <pv>  langfuse/<pvc>  <size>  <node>  <path>
```

Confirm the cluster symptoms:

```bash
kubectl -n langfuse get pod,sts,endpoints -o wide
kubectl -n langfuse describe pod <affected-pod>
kubectl get pv <affected-pv> -o yaml
```

Evidence to capture in the incident note:

- affected pod, PVC, PV, node, and expected path;
- kubelet event text;
- whether the directory exists on the owner node;
- whether a matching historical directory exists on another node or backup;
- whether the data is disposable or must be restored.

## Decision tree

1. If a matching directory exists on the owner node, do not recreate it. Diagnose
   permissions, node health, and kubelet/local-path behavior.
2. If a matching directory exists on another node or backup, restore it to the PV
   owner node before allowing the workload to start.
3. If no data exists and the operator accepts data loss, recreate only the exact
   missing directory for the exact PV path.
4. If data loss is not acceptable and no backup exists, stop and escalate. Do not
   let an empty ClickHouse or ZooKeeper directory silently replace expected data.

## Disposable recovery pattern

This pattern is only for operator-approved disposable recovery. It is not a
general repair command.

For a missing local-path base directory:

```bash
ssh <node> 'install -d -m 700 -o root -g root /var/lib/rancher/k3s/storage'
```

For each approved disposable PV path:

```bash
ssh <node> 'install -d -m 2777 -o root -g 1001 /var/lib/rancher/k3s/storage/<pv-dir>'
ssh <node> 'stat -c "%a %u:%g %n" /var/lib/rancher/k3s/storage/<pv-dir>'
```

Use the group already observed for the chart's healthy sibling PVCs. During the
2026-07-09 incident, healthy Langfuse paths were `2777 0:1001`.

Do not recursively change ownership or permissions across
`/var/lib/rancher/k3s/storage`.

## Validation

After recovery:

```bash
kubectl -n langfuse get pod,deploy,sts,endpoints -o wide
kubectl -n langfuse logs deploy/langfuse-web --tail=160
kubectl -n argocd get application langfuse -o wide
curl -k -I https://langfuse.rbx.ia.br
```

Exit criteria:

- ClickHouse StatefulSet is `1/1`.
- ZooKeeper StatefulSet is `3/3`.
- `langfuse-web` is `1/1`.
- `langfuse` ArgoCD Application is `Synced` and `Healthy`.
- Public HTTPS returns `200`.

## Structural follow-up

`local-path` is node-local storage, not a high-availability storage layer. For
Langfuse, choose one of these before treating observability as durable:

1. Scheduled backups for ClickHouse, ZooKeeper, and Redis PVC data with a tested
   restore path.
2. Migration of ClickHouse/ZooKeeper state to a replicated storage class.
3. External managed or self-managed telemetry stores with their own backup and
   restore procedure.

Until one of those exists, run `scripts/audit-local-path-pv-dirs.sh langfuse`
after node maintenance, k3s upgrades, local-path incidents, and any migration
that touches `/var/lib/rancher/k3s/storage`.
