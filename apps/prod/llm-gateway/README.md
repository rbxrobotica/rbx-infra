# llm-gateway — LiteLLM Proxy (Experimental)

**Status:** Candidate for internal LLM Gateway. Not production-critical.
**Criticality:** Non-critical. Safe to scale to zero or remove.
**Exposure:** Internal only (ClusterIP). No Ingress in this revision.

---

## Purpose

Evaluate [LiteLLM Proxy](https://docs.litellm.ai/docs/proxy/configs) as a centralized LLM Gateway for RBX internal workloads:

- Unified API surface for multiple LLM providers (OpenAI, Anthropic, etc.)
- Request routing, aliases, and (future) spend tracking
- Virtual key management via external Postgres

---

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Internal Client│────▶│  litellm Service │────▶│  LiteLLM Proxy  │
│  (cluster pod)  │     │  ClusterIP:4000  │     │  Port 4000      │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
                              ┌───────────────────────────┼──────────┐
                              │                           │          │
                              ▼                           ▼          ▼
                      ┌──────────────┐           ┌────────────┐  ┌──────────┐
                      │ ConfigMap    │           │ Secret     │  │ Postgres │
                      │ proxy_config │           │ API keys   │  │ (jaguar) │
                      └──────────────┘           └────────────┘  └──────────┘
```

---

## Files

| File | Purpose |
|------|---------|
| `namespace.yml` | Namespace reference for kustomization |
| `litellm-deploy.yml` | Deployment (1 replica, Recreate, internal only) |
| `litellm-svc.yml` | ClusterIP service on port 4000 |
| `litellm-config.yml` | ConfigMap with `proxy_config.yaml` (placeholders) |
| `secrets.example.yml` | **Example only** — never synced by ArgoCD. Documental reference for secret structure. |
| `postgres-svc.yml` | Service + Endpoints routing to external ParadeDB on jaguar |
| `rbac.yml` | ServiceAccount only (no Role/RoleBinding; LiteLLM does not need K8s API access) |
| `network-policy.yml` | Default-deny ingress from outside cluster |
| `kustomization.yml` | Kustomize root (does NOT include `secrets.example.yml`) |

---

## Decisions

### No Ingress (yet)
The first revision is intentionally **not** exposed via Traefik Ingress.
Rationale:
1. The service is experimental — we do not want public attack surface.
2. No DNS record needed, no TLS certificate complexity.
3. Validation is done via `kubectl port-forward` from an operator workstation.

Future: If promoted to production-critical, add `litellm-ingress.yml` + DNS record + `middleware-https.yml` following the standard pattern in `docs/runbooks/ADD-NEW-APPLICATION.md`.

### No Helm Chart
LiteLLM publishes a Helm chart, but RBX infra currently uses raw Kubernetes manifests + Kustomize for all applications. A Helm migration would be a cross-repo decision (ADR), not a one-off exception.

### External Postgres on Jaguar
Per `docs/infra/ARCHITECTURE.md`:

> **PostgreSQL never runs inside the production k3s cluster.**

The LiteLLM Proxy connects to a **local Service** (`litellm-postgres`) inside the `llm-gateway` namespace. This Service has no selector; instead, an `Endpoints` object explicitly routes traffic to jaguar (`161.97.147.76:5432`).

This pattern keeps the app config decoupled from the external host IP and allows RBX to change the database location without touching the Deployment or Secret.

The `litellm` database and user must be provisioned by Ansible on jaguar before first deploy.

### Recreate Strategy
`strategy: Recreate` ensures only one pod is active at a time. This avoids:
- Multiple proxies competing for the same master key rotation
- DB migration races (LiteLLM auto-migrates on startup)

### Placeholder Model Aliases
`proxy_config.yaml` defines two placeholder aliases (`openai-gpt-4o-placeholder`, `anthropic-claude-sonnet-placeholder`).
These map to real upstream models but use placeholder API keys by default.
After real keys are injected into `pass` + secret, the aliases work without a config change.

### Conservative Resources
| | Value | Rationale |
|---|---|---|
| Request CPU | 50m | LiteLLM proxy is I/O-bound |
| Request Memory | 128Mi | Base footprint |
| Limit CPU | 250m | Burst capacity for config reloads |
| Limit Memory | 256Mi | Headroom for connection buffers |

These can be adjusted after observing real usage.

### Network Policy
`network-policy.yml` allows:
- Ingress: any pod inside the cluster → port 4000
- Egress: DNS (UDP 53), HTTPS (TCP 443), Postgres (TCP 5432)

It blocks external ingress explicitly. If an Ingress is added later, the NetworkPolicy must be updated to allow `namespaceSelector` matching `kube-system` (Traefik) or the ingress controller namespace.

**DNS label validation pending:** The rule assumes `k8s-app: kube-dns`. Verify this matches your cluster before deploying.

---

## Bootstrap Checklist

Before ArgoCD syncs for the first time:

- [ ] Ansible DB provisioning created `litellm` user + database on jaguar (`paradedb` role)
- [ ] Pass entries created under `rbx/llm-gateway/`:
  - `db-password` (openssl rand -hex 32 — hex avoids URL-unsafe characters)
  - `master-key` (openssl rand -hex 32)
  - `salt-key` (openssl rand -hex 32)
  - `openai-api-key` (real key or placeholder)
  - `anthropic-api-key` (real key or placeholder)
- [ ] `init-vault-from-pass.sh` updated to include `paradedb_litellm_password`
- [ ] Ansible `k8s-secrets` role creates `litellm-secrets` in namespace `llm-gateway`
- [ ] Image tag in `litellm-deploy.yml` **pinned to a known SHA or stable tag** (not a floating tag)
- [ ] Confirm node architecture before deploy:
  `kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.nodeInfo.architecture}{"\n"}{end}'`
- [ ] `gitops/projects/rbx-applications.yaml` includes `llm-gateway` destination
- [ ] `core/namespaces/llm-gateway.yml` created
- [ ] ArgoCD Application `llm-gateway.yml` created in `gitops/app-of-apps/`

The current LiteLLM image digest was validated for `amd64`. If any cluster node reports `arm64`, review whether this deployment should use the multi-arch digest instead of the amd64 digest before syncing ArgoCD.

---

## Validation

See `docs/runbooks/LLM-GATEWAY-VALIDATION.md` for step-by-step validation via port-forward.

---

## Rollback

See `docs/runbooks/LLM-GATEWAY-ROLLBACK.md` for disabling, scaling to zero, or full removal.

---

## Risks and Open Questions

| Risk | Mitigation | Owner |
|------|-----------|-------|
| Image pinned to `v1.83.14-stable@sha256:d6401c00…` | Verify cosign signature before any upgrade; confirm node architecture matches amd64 digest below | Infra |
| LiteLLM auto-migrates DB on boot | Monitor startup logs; Recreate strategy limits races | Infra |
| Master key rotation not automated | Document manual rotation in secrets runbook | Security |
| No high-availability | Single replica acceptable for experiment; add HPA later | Infra |
| Provider keys stored in same Secret as DB creds | Acceptable for experiment; split into multiple ExternalSecrets if promoted | Security |
| Spend tracking disabled | `store_model_in_db: false` — enable after DB smoke test | Product |
| No rate limiting | Add `router_settings` + Redis if promoted | Infra |
| DNS NetworkPolicy label may not match cluster | Verify `k8s-app: kube-dns` before first deploy | Infra |

### Architecture Note

The pinned digest `sha256:d6401c00…` corresponds to the **amd64** platform manifest.
Before deploying, confirm that your cluster nodes run `amd64`:

```bash
kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.nodeInfo.architecture}{"\n"}{end}'
```

If any node reports `arm64`, consider using the multi-arch digest or the arm64-specific digest
(`sha256:1bdc7fd7d6634bbf693d837b14ae41a49d7970cacecb5a0d89db401a825d05e1`).
In most cases, Kubernetes will pull the correct platform automatically when using the tag alone
(`v1.83.14-stable`), but digest pinning requires an explicit platform choice.
