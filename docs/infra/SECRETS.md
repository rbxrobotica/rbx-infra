# Secrets Management

**Date:** 2026-04-09
**Status:** Active

## Model

`pass` (GPG-encrypted git repo) is the single source of truth for all secrets.
Nothing sensitive is committed to any git repository in plaintext.

Two consumers read from `pass`:

| Consumer | How | What it produces |
|----------|-----|-----------------|
| Ansible | `bootstrap/scripts/init-vault-from-pass.sh` generates `vault.yml` | DB users + passwords on VPS hosts |
| Ansible `k8s-secrets` role | reads pass directly at bootstrap time | Kubernetes `Secret` objects |

ArgoCD never reads secrets directly. It manages only non-sensitive manifests.
The Kubernetes `Secret` objects created by Ansible are the runtime source for pods.

## pass Namespace Structure

```
rbx/
  cluster/
    ghcr-token                  # GitHub PAT — read:packages + write:packages (rbxrobotica org)
  robson/
    db-password                 # PostgreSQL password for user `robson` (robsond production)
    projection-tenant-id        # UUID, generated once, stable across reinstalls (production)
    binance-api-key             # Binance production API key (real exchange)
    binance-api-secret          # Binance production API secret (real exchange)
    api-token                   # Bearer token for robsond HTTP API — openssl rand -hex 32
  robson-testnet/
    binance-api-key             # Binance testnet API key (testnet.binance.vision only)
    binance-api-secret          # Binance testnet API secret
    db-password                 # PostgreSQL password for user `robson_testnet` on jaguar — MUST be hex (openssl rand -hex 32)
    projection-tenant-id        # UUID, generated once on first testnet bootstrap
    api-token                   # Bearer token for robsond HTTP API — openssl rand -hex 32
  truthmetal/
    db-password                 # PostgreSQL password for user `truthmetal`
  memory/
    token                       # Bearer token for rbx-memory
  observability/
    token                       # Bearer token for rbx-observability
    langfuse-host               # Langfuse host URL
    langfuse-public-key         # Langfuse public key
    langfuse-secret-key         # Langfuse secret key
  data/
    token                       # Bearer token for rbx-data
    warehouse-dsn               # External warehouse DSN
  llm-gateway/
    db-password                 # PostgreSQL password for user `litellm` on jaguar — MUST be hex (openssl rand -hex 32)
    master-key                  # LiteLLM proxy master key — openssl rand -hex 32
    salt-key                    # LiteLLM DB encryption salt — openssl rand -hex 32
    groq-api-key                # Groq API key (free tier available — good for smoke tests)
    zai-api-key                 # Z.AI / ZhipuAI API key (GLM models)
    moonshot-api-key            # Moonshot API key (Kimi models)
    # Future candidates (not required for initial bootstrap):
    # deepseek-api-key          # DeepSeek API key
    # qwen-api-key              # DashScope / Qwen API key
    # openai-api-key            # OpenAI API key
    # anthropic-api-key         # Anthropic API key
  monitoring/
    grafana-admin-password      # Grafana admin UI password
  dns/
    pdns-api-key                # PowerDNS REST API key (pantera — primary)
    pdns-db-password            # PostgreSQL password for user `pdns`
  data/
    warehouse-db-password       # PostgreSQL password for user `rbx_data` on jaguar — rbx_data_warehouse DB
    warehouse-dsn               # Full DSN postgres://rbx_data:<password>@161.97.147.76:5432/rbx_data_warehouse (k8s ExternalSecret source)
```

**Environment separation rule:** `rbx/robson/` paths are for production only. `rbx/robson-testnet/` paths are for testnet only. The Ansible `k8s-secrets` role must never cross-reference these namespaces — the production task reads only from `rbx/robson/`, the testnet task reads only from `rbx/robson-testnet/`.

All keys are plain text (one secret per file). No JSON/YAML inside pass entries.

## rbx-data Warehouse Database

RBX keeps a **single Postgres server** (`jaguar`, `db_server`, `161.97.147.76:5432`).
The warehouse is a **new database inside it** (`rbx_data_warehouse`), satisfying the Postgres-external invariant.

| Item | Value |
|------|-------|
| Host | `jaguar` (`db_server`, `161.97.147.76:5432`) |
| Database | `rbx_data_warehouse` |
| User | `rbx_data` |

Two pass keys are involved:

| pass key | Purpose |
|----------|---------|
| `rbx/data/warehouse-db-password` | The DB role password. Used by the `rbx-data-warehouse-db` Ansible role to create the user and by `vault.yml`. |
| `rbx/data/warehouse-dsn` | The DSN the operator constructs as `postgres://rbx_data:<password>@161.97.147.76:5432/rbx_data_warehouse`. This is the k8s `ExternalSecret` source for pods that need warehouse access. |

## Day-0 Setup (First Time)

Run once on the operator's machine before any Ansible or cluster bootstrap.

```bash
# 1. Initialize pass (if not done)
gpg --gen-key   # or use existing key
pass init <GPG_KEY_ID>

# 2. Create required entries
# Generate passwords with: openssl rand -base64 24

pass insert rbx/cluster/ghcr-token          # GitHub PAT from github.com/settings/tokens
pass insert rbx/robson/db-password          # openssl rand -base64 24
pass insert rbx/robson/binance-api-key      # Binance production API key
pass insert rbx/robson/binance-api-secret   # Binance production API secret
pass insert rbx/robson/api-token            # openssl rand -hex 32
pass insert rbx/truthmetal/db-password      # openssl rand -base64 24
pass insert rbx/monitoring/grafana-admin-password  # openssl rand -base64 18
pass insert rbx/dns/pdns-api-key                   # openssl rand -base64 24
pass insert rbx/dns/pdns-db-password               # openssl rand -base64 24
pass insert rbx/data/warehouse-db-password         # openssl rand -base64 24
pass insert rbx/data/warehouse-dsn                 # constructed as postgres://rbx_data:<password>@161.97.147.76:5432/rbx_data_warehouse

# projection-tenant-id is auto-generated by the k8s-secrets role on first bootstrap
# (stored back into pass automatically — no manual step needed)
```

### Day-0 Setup: Robson testnet (additional)

Run once when bootstrapping the `robson-testnet` environment for the first time.
Binance testnet credentials are obtained from https://testnet.binance.vision — requires a Binance account.

```bash
# Binance testnet credentials (from testnet.binance.vision dashboard)
pass insert rbx/robson-testnet/binance-api-key     # from Binance testnet UI
pass insert rbx/robson-testnet/binance-api-secret  # from Binance testnet UI

# Database password for robson_testnet PostgreSQL user
# IMPORTANT: use hex only — base64 passwords contain / + = which break URL parsing
pass insert rbx/robson-testnet/db-password         # openssl rand -hex 32

# API bearer token for robsond HTTP API (/arm, /signal, /approve, /disarm, /panic)
pass insert rbx/robson-testnet/api-token           # openssl rand -hex 32

# projection-tenant-id is auto-generated by the k8s-secrets testnet task on first bootstrap
# (stored back into pass automatically — no manual step needed)
```

The `rbx/robson-testnet/` keys are **completely separate** from `rbx/robson/`. They are sourced from a different exchange account context (testnet, not production) and must never be mixed.

## Ansible Bootstrap (vault.yml)

Ansible roles use `vault.yml` (gitignored) for DB passwords on host provisioning.
Generate it from pass before running `ansible-playbook`:

```bash
cd /path/to/rbx-infra
bash bootstrap/scripts/init-vault-from-pass.sh
ansible-playbook bootstrap/ansible/site.yml -i bootstrap/ansible/inventory/hosts.yml
```

The script reads from pass and writes `bootstrap/ansible/group_vars/all/vault.yml`.
The file is ephemeral — regenerate it any time from pass.

## Kubernetes Secrets Bootstrap

The Ansible `k8s-secrets` role (Phase 10 in `site.yml`) creates Kubernetes secrets
from pass after the cluster is running. It runs on `localhost` and uses
`~/.kube/config-rbx` (fetched by the `k3s-server` role).

Secrets created per namespace:

| Namespace | Secret name | Keys | pass source |
|-----------|-------------|------|-------------|
| `robson` | `robsond-secret` | `database-url`, `projection-tenant-id`, `binance-api-key`, `binance-api-secret`, `api-token` | `rbx/robson/*` |
| `robson` | `ghcr-pull-secret` | docker registry credentials | `rbx/cluster/ghcr-token` |
| `robson-testnet` | `robsond-testnet-secret` | `database-url`, `projection-tenant-id`, `binance-api-key`, `binance-api-secret`, `api-token` | `rbx/robson-testnet/*` |
| `robson-testnet` | `ghcr-pull-secret` | docker registry credentials | `rbx/cluster/ghcr-token` |
| `rbx-console` | `ghcr-pull-secret` | docker registry credentials | `rbx/cluster/ghcr-token` |
| `rbx-ia-br` | `rbx-memory-token` | `token` | `rbx/memory/token` |
| `rbx-ia-br` | `rbx-observability-token` | `token` | `rbx/observability/token` |
| `rbx-ia-br` | `rbx-observability-langfuse` | `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | `rbx/observability/langfuse-host`, `rbx/observability/langfuse-public-key`, `rbx/observability/langfuse-secret-key` |
| `rbx-ia-br` | `rbx-data-token` | `token` | `rbx/data/token` |
| `rbx-ia-br` | `rbx-data-warehouse` | `dsn` | `rbx/data/warehouse-dsn` |
| `monitoring` | `grafana-admin` | `admin-user`, `admin-password` | `rbx/monitoring/grafana-admin-password` |

`rbx-ia-br` is the central vault namespace read by the reorg services'
ExternalSecrets through the `kubernetes-store` SecretStore. The
`contabo-s3-credentials` secret in `rbx-ia-br` is pre-existing and reused by
`rbx-memory` and `rbx-data`; this role does not provision or modify it.

Run idempotently — safe to re-run after a cluster wipe.

**Isolation invariant:** The `k8s-secrets` role must have two independent task blocks — one for production (`robson` namespace, reads `rbx/robson/`) and one for testnet (`robson-testnet` namespace, reads `rbx/robson-testnet/`). These blocks must never share pass key references. See `docs/ROBSON-TESTNET-ENVIRONMENT.md` for the full Ansible task specification.

## Reinstall from Scratch

To rebuild the entire cluster from zero:

```bash
# 1. Ensure pass is unlocked and all keys exist (see Day-0 Setup)
pass show rbx/cluster/ghcr-token

# 2. Generate vault.yml from pass
bash bootstrap/scripts/init-vault-from-pass.sh

# 3. Run full Ansible playbook (provisions VPS + creates k8s secrets)
ansible-playbook bootstrap/ansible/site.yml -i bootstrap/ansible/inventory/hosts.yml

# 4. ArgoCD syncs everything else automatically from rbx-infra git
```

No manual `kubectl create secret` or `kubectl apply` needed after step 3.

## Adding Secrets for New Applications

1. Add pass entries under `rbx/<app-name>/`:
   ```bash
   pass insert rbx/newapp/db-password
   ```

2. Add a task block to `bootstrap/ansible/roles/k8s-secrets/tasks/main.yml`

3. Add the pass key path to `bootstrap/ansible/roles/k8s-secrets/defaults/main.yml`

4. Update `bootstrap/scripts/init-vault-from-pass.sh` if the app also needs Ansible provisioning

5. Document the new keys in this file under **pass Namespace Structure**

## Security Notes

- The GPG private key used for pass is the root secret. Back it up offline (paper/hardware).
- `vault.yml` is gitignored and ephemeral. Never commit it.
- `pass` entries containing DB passwords should use `openssl rand -base64 24` (32 bytes entropy).
- The `ghcr-token` PAT should have minimum required scopes: `read:packages` for pull secrets.
  CI/CD uses its own `GITOPS_TOKEN` (configured in GitHub repo secrets, not in pass).
- `projection-tenant-id` is a stable UUID. Changing it on reinstall means the projection
  tables start empty (OK for a wiped cluster — the EventLog is also gone).
