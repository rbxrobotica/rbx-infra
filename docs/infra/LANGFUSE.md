# Langfuse Self-Hosting

**Status:** deployed, degraded as of 2026-07-08, manual-sync GitOps Application

## Current Health Note - 2026-07-08

Read-only cluster diagnostics on 2026-07-08 found Langfuse unhealthy:

- `langfuse-clickhouse-shard0-0` is stuck in `ContainerCreating`.
- `langfuse-zookeeper-1` and `langfuse-zookeeper-2` are stuck in
  `ContainerCreating`.
- kubelet reports missing `local-path` volume directories under
  `/var/lib/rancher/k3s/storage/`.
- `langfuse-web` is in `CrashLoopBackOff`.

Treat this as a storage and recovery incident, not as a simple pod restart.
Do not delete PVCs, recreate StatefulSets, or sync the ArgoCD Application until
an operator has chosen the recovery path and accepted the data-loss risk.

## Topology

Langfuse is deployed in the RBX production cluster through the official Helm chart.

| Component | Placement | Notes |
|-----------|-----------|-------|
| Langfuse web | In-cluster, namespace `langfuse` | Exposed at `https://langfuse.rbx.ia.br` through Traefik and cert-manager TLS |
| Langfuse worker | In-cluster, namespace `langfuse` | Deployed by the chart |
| ClickHouse | In-cluster, bundled chart dependency | PVC-backed; password sourced from `langfuse-clickhouse-auth` |
| Valkey/Redis | In-cluster, bundled chart dependency | PVC-backed; password sourced from `langfuse-redis-auth` |
| PostgreSQL | External on jaguar (`161.97.147.76`) | Database `langfuse`, user `langfuse`, provisioned by Ansible |
| Blob storage | Contabo S3 (`https://eu2.contabostorage.com`) | Bucket `rbx-langfuse`; credentials sourced from `langfuse-s3-auth` |

The ArgoCD Application is intentionally manual-sync. Do not enable automated sync until the database, Kubernetes secrets, bucket, and first render have been reviewed.

## Recovery Requirements

Before any repair:

1. Identify the owning node and expected path for each Langfuse PVC.
2. Check whether the missing local-path directories exist in backup or on a
   former node.
3. Decide whether ClickHouse/ZooKeeper data is disposable or must be restored.
4. Record the chosen path in a short incident note.
5. Restore or rebuild through an approved operator flow.

Longer term, do not treat `local-path` as HA storage. If Langfuse is a durable
observability dependency, move state to an external/replicated backend or keep a
tested backup and restore path for every PVC.

## Required Pass Keys

Create these before running the Ansible bootstrap:

```bash
pass insert rbx/langfuse/db-password
pass insert rbx/langfuse/nextauth-secret
pass insert rbx/langfuse/salt
pass insert rbx/langfuse/encryption-key
pass insert rbx/langfuse/clickhouse-password
pass insert rbx/langfuse/redis-password
pass insert rbx/s3/access-key
pass insert rbx/s3/secret-key
```

Generation guidance:

| Key | Suggested generator |
|-----|---------------------|
| `rbx/langfuse/db-password` | `openssl rand -hex 32` |
| `rbx/langfuse/nextauth-secret` | `openssl rand -hex 32` |
| `rbx/langfuse/salt` | `openssl rand -hex 32` |
| `rbx/langfuse/encryption-key` | `openssl rand -hex 32` |
| `rbx/langfuse/clickhouse-password` | `openssl rand -hex 32` |
| `rbx/langfuse/redis-password` | `openssl rand -hex 32` |

## Provisioning Order

1. Add the pass entries above.
2. Regenerate `bootstrap/ansible/group_vars/all/vault.yml` with `bootstrap/scripts/init-vault-from-pass.sh`.
3. Run the Ansible database and k8s secret provisioning through the approved operator flow.
4. Review and manually sync the `langfuse` ArgoCD Application.

## Post-Deploy Observability Handoff

After Langfuse is reachable:

1. Open `https://langfuse.rbx.ia.br`.
2. Create the RBX project in the Langfuse UI.
3. Create project API keys.
4. Store the integration keys for `rbx-observability`:

```bash
pass insert rbx/observability/langfuse-host       # https://langfuse.rbx.ia.br
pass insert rbx/observability/langfuse-public-key
pass insert rbx/observability/langfuse-secret-key
```

Do not commit API keys, generated secrets, exported project keys, or rendered Secret manifests.
