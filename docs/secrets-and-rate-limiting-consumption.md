# Secrets and Rate-Limiting Consumption Guide

**Audience**: Operators and service authors integrating with rbx-infra.

**Governance**: ADR-0010 (Thalamus slim-down), ADR-0008 (Agentic MCP Governance).

---

## Overview

rbx-infra owns secrets management and ingress rate-limiting for the entire RBX
ecosystem. Services **consume** these — they never own, generate, or embed them.

This guide explains how a service declares what it needs and how rbx-infra
delivers it.

## How to add secrets for a new service

### 1. Create `pass` entries

```bash
pass insert rbx/<service-name>/db-password       # openssl rand -hex 32
pass insert rbx/<service-name>/api-token           # openssl rand -hex 32
# ... additional keys as needed
```

### 2. Update the Ansible `k8s-secrets` role

Add a task block to `bootstrap/ansible/roles/k8s-secrets/tasks/main.yml` that:
- Creates a Kubernetes `Secret` in the service's namespace.
- Reads values from `pass` via `lookup('passwordstore', ...)`.
- Keys in the K8s Secret become env var names injected into pods.

### 3. Reference env vars in the workload manifest

In the service's `Deployment` (managed by rbx-infra GitOps):

```yaml
# EXAMPLE ONLY — not a real manifest
env:
  - name: DATABASE_URL
    valueFrom:
      secretKeyRef:
        name: <service>-secret
        key: database-url
  - name: S3_ACCESS_KEY_ID
    valueFrom:
      secretKeyRef:
        name: <service>-secret
        key: s3-access-key-id
```

### 4. Document the new keys

Add entries to `docs/infra/SECRETS.md` under **pass Namespace Structure**.

## Consumption contract

All secrets are delivered as **environment variables**. Services must:

- Read secrets **only** from env vars (`std::env::var`, `env!`, `option_env!`).
- **Never** hard-code, log, or echo secret values.
- **Never** write secrets to disk, ConfigMaps, or unencrypted stores.
- Crash on missing required secrets at startup (fail-fast).

### Placeholder env var names (no real values)

| Env var | Consumer | Source (pass path pattern) |
|---|---|---|
| `DATABASE_URL` | Robson, rbx-memory, rbx-data | `rbx/<service>/db-password` |
| `BINANCE_API_KEY` | Robson | `rbx/robson/binance-api-key` |
| `BINANCE_API_SECRET` | Robson | `rbx/robson/binance-api-secret` |
| `API_TOKEN` | Robson | `rbx/robson/api-token` |
| `PROJECTION_TENANT_ID` | Robson | `rbx/robson/projection-tenant-id` |
| `MODEL_PROVIDER_API_KEY` | Thalamus, LiteLLM | `rbx/llm-gateway/<provider>-api-key` |
| `LITELLM_MASTER_KEY` | LiteLLM | `rbx/llm-gateway/master-key` |
| `LITELLM_SALT_KEY` | LiteLLM | `rbx/llm-gateway/salt-key` |
| `LITELLM_DB_PASSWORD` | LiteLLM | `rbx/llm-gateway/db-password` |
| `S3_ACCESS_KEY_ID` | rbx-memory, rbx-data | per-service S3 credentials |
| `S3_SECRET_ACCESS_KEY` | rbx-memory, rbx-data | per-service S3 credentials |

## Rate-limiting

Rate-limits are enforced at the **nginx ingress controller** (self-hosted,
in-cluster). **No Cloudflare.** No external CDN/WAF.

### How limits are applied

1. rbx-infra declares per-service `Ingress` resources with rate-limit annotations.
2. Services **do not** set their own rate-limits — they are applied externally.
3. Limits are tuned per-route based on operational requirements.

### Declaring rate-limits for a new service

Add annotations to the service's `Ingress` manifest in rbx-infra:

```yaml
# EXAMPLE ONLY — not a real manifest
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: <service>-ingress
  namespace: <service>-ns
  annotations:
    nginx.ingress.kubernetes.io/limit-rps: "50"
    nginx.ingress.kubernetes.io/limit-connections: "20"
    nginx.ingress.kubernetes.io/limit-burst: "100"
spec:
  rules:
    - host: <service>.rbx.ia.br
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: <service>
                port:
                  number: 8080
```

### Adjusting existing limits

Edit the `Ingress` annotations in the rbx-infra GitOps repo. ArgoCD syncs the
change. No restart required — nginx picks up annotation changes on resync.

## Example: ExternalSecret / SealedSecret template

If migrating to Sealed Secrets or External Secrets Operator in the future,
the pattern would look like this:

```yaml
# EXAMPLE ONLY — not applied, no real values
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: <service>-secret
  namespace: <service>-ns
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: rbx-pass-store
    kind: ClusterSecretStore
  target:
    name: <service>-secret
    creationPolicy: Owner
  data:
    - secretKey: database-url
      remoteRef:
        key: rbx/<service>/db-password
    - secretKey: api-token
      remoteRef:
        key: rbx/<service>/api-token
```

This is **not implemented**. The current mechanism is `pass` → Ansible → K8s
Secrets as documented in `docs/infra/SECRETS.md`. This template exists to
illustrate the migration path if External Secrets Operator is adopted.

## Checklist for new service onboarding

- [ ] Create `pass` entries under `rbx/<service-name>/`
- [ ] Add Ansible `k8s-secrets` task block
- [ ] Add K8s Secret name to Ansible defaults
- [ ] Reference env vars in workload Deployment
- [ ] Add Ingress manifest with rate-limit annotations
- [ ] Document new keys in `docs/infra/SECRETS.md`
- [ ] Update this file's placeholder table if new env var names are introduced
