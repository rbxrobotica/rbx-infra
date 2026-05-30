# ADR-0006: Secrets and Rate-Limiting Ownership per ADR-0010

**Status**: Proposed

**Date**: 2026-05-30

**Governance anchors**:
[ADR-0010](https://github.com/rbxrobotica/rbx-governance/blob/main/docs/adr/ADR-0010-thalamus-slim-down-and-ecosystem-reorganization.md) (Thalamus slim-down),
[ADR-0008](https://github.com/rbxrobotica/rbx-governance/blob/main/docs/adr/ADR-0008-agentic-mcp-governance-and-internal-domain-mcps.md) (Agentic MCP Governance).

---

## Context

ADR-0010 extracts secrets management and rate-limiting from Thalamus into
`rbx-infra`. Thalamus and all other RBX services **consume** secrets and are
subject to rate-limits — they do not own them. This local ADR records the
ownership boundary and consumption contract within rbx-infra.

The existing secrets model (`pass` → Ansible → Kubernetes Secrets) is documented
in `docs/infra/SECRETS.md`. This ADR formalizes the ADR-0010 boundary on top of
that existing practice.

## Decision

### rbx-infra owns

1. **API / model provider keys** — stored in `pass`, materialised as Kubernetes
   `Secret` objects by the Ansible `k8s-secrets` role. Keys include all entries
   under `rbx/llm-gateway/` (e.g. `groq-api-key`, `zai-api-key`,
   `moonshot-api-key`) and any future model-provider credentials.

2. **Rate-limit policies at the ingress layer** — enforced by the in-cluster
   nginx ingress controller (or equivalent). Per-service and per-route limits
   are declared in rbx-infra manifests. **No Cloudflare.** Self-hosted
   in-cluster only.

### Consumers receive, never embed

| Consumer | What it receives | Mechanism |
|---|---|---|
| Thalamus (AI control plane) | Model provider API keys | Injected env vars from K8s Secrets |
| LiteLLM (model gateway) | Provider keys + master key | Injected env vars from K8s Secrets |
| Robson | Binance keys, DB URL, API token | Injected env vars from K8s Secrets |
| rbx-memory | S3 credentials, Postgres URL | Injected env vars from K8s Secrets |
| rbx-data | S3 credentials, Postgres URL | Injected env vars from K8s Secrets |
| All services | Rate-limit policies | Ingress annotations (read-only) |

**No service embeds secret values in code, config maps, or container images.**
All secrets arrive via environment variables populated from Kubernetes `Secret`
objects created by rbx-infra's Ansible roles.

### Consumption contract: environment variable names

The following placeholder env var names define the consumption contract.
**No real values are listed here.** See `docs/infra/SECRETS.md` for the `pass`
source paths.

#### Thalamus / LiteLLM (AI control plane + model gateway)

| Env var placeholder | Purpose |
|---|---|
| `MODEL_PROVIDER_API_KEY` | Generic model-provider API key (per-provider instances) |
| `LITELLM_MASTER_KEY` | LiteLLM proxy authentication key |
| `LITELLM_SALT_KEY` | LiteLLM DB encryption salt |
| `LITELLM_DB_PASSWORD` | LiteLLM Postgres user password |

#### Robson

| Env var placeholder | Purpose |
|---|---|
| `DATABASE_URL` | External Postgres connection string |
| `BINANCE_API_KEY` | Binance exchange API key |
| `BINANCE_API_SECRET` | Binance exchange API secret |
| `PROJECTION_TENANT_ID` | Stable UUID for event projection |
| `API_TOKEN` | Bearer token for robsond HTTP API |

#### rbx-memory / rbx-data

| Env var placeholder | Purpose |
|---|---|
| `S3_ACCESS_KEY_ID` | S3 bucket credentials |
| `S3_SECRET_ACCESS_KEY` | S3 bucket credentials |
| `DATABASE_URL` | External Postgres connection string |

### Rate-limiting contract

Rate-limits are applied at the nginx ingress layer via annotations on `Ingress`
resources managed by rbx-infra. Services declare their desired limits via
rbx-infra manifest values (not in their own repos).

Example annotation placeholders (see
`docs/secrets-and-rate-limiting-consumption.md` for the full guide):

```yaml
# NOT a real manifest — example only
annotations:
  nginx.ingress.kubernetes.io/limit-rps: "50"
  nginx.ingress.kubernetes.io/limit-connections: "20"
```

## Consequences

- Thalamus and other services never touch `pass` or Ansible — they consume
  injected env vars.
- Rate-limit policies are centralised in rbx-infra manifests, consistent with
  the "rbx-infra owns ingress" boundary.
- Adding a new provider key requires: (1) `pass insert`, (2) Ansible task update,
  (3) workload env var reference — no code changes in consumer services.

## Boundaries

- rbx-infra **is** the secrets management owner (pass → Ansible → K8s Secrets)
  and the ingress rate-limit owner.
- rbx-infra **is not** the consumer of model keys at runtime (Thalamus / LiteLLM
  are), nor does it decide which model to route to (that is Thalamus control
  plane).
