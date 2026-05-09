# LLM Gateway Validation Runbook

**Audience:** RBX engineers validating the LiteLLM Proxy deployment.
**Prerequisites:** `kubectl` access, `KUBECONFIG=~/.kube/config-rbx`, `curl`.
**Scope:** Internal-only validation via port-forward. No public exposure.

> **Warning:** LiteLLM Proxy is **experimental** and not production-critical.
> Do not expose it via Ingress or DNS until it graduates from the evaluation phase.

---

## 1. Verify ArgoCD Sync

```bash
export KUBECONFIG=~/.kube/config-rbx

# Check application health
kubectl get application llm-gateway -n argocd

# Watch sync progress
kubectl get application llm-gateway -n argocd -w
```

Expected: `Healthy` and `Synced`.

---

## 2. Verify Pod Readiness

```bash
kubectl get pods -n llm-gateway -l app.kubernetes.io/name=litellm

# Deep dive if not Ready
kubectl describe pod -n llm-gateway -l app.kubernetes.io/name=litellm
kubectl logs -n llm-gateway -l app.kubernetes.io/name=litellm --tail=100
```

Expected: `1/1 Ready`, no `CrashLoopBackOff`.

> **Note:** Startup can take up to ~150s because of conservative probe settings
> (`initialDelaySeconds: 120`). This accommodates DB auto-migration on first boot.

---

## 3. Port-Forward and Health Checks

```bash
# Terminal 1 — forward local 4000 to the proxy
kubectl port-forward -n llm-gateway svc/litellm 4000:4000

# Terminal 2 — health probes
curl -s http://localhost:4000/health/liveliness | jq .
curl -s http://localhost:4000/health/readiness | jq .
```

Expected: HTTP 200 with JSON body.

---

## 4. Validate Config Load

```bash
curl -s http://localhost:4000/v1/models \
  -H "Authorization: Bearer $(kubectl get secret -n llm-gateway litellm-secrets -o jsonpath='{.data.master-key}' | base64 -d)"
```

Expected: JSON list containing `openai-gpt-4o-placeholder` and `anthropic-claude-sonnet-placeholder`.

> **Security note:** This reads the master key from the cluster secret. Do not share the bearer token.

---

## 5. Smoke Test (Optional — Requires Real Provider Keys)

If real provider keys are configured in `litellm-secrets`:

```bash
# OpenAI placeholder
curl -X POST http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <MASTER_KEY>" \
  -d '{
    "model": "openai-gpt-4o-placeholder",
    "messages": [{"role": "user", "content": "Say hello"}]
  }'
```

If keys are still placeholders, expect a 401/403 from the upstream provider — this confirms the proxy is routing correctly.

---

## 6. Cleanup Port-Forward

Stop `kubectl port-forward` with `Ctrl+C`. No cluster state is changed.

---

## Pre-Deploy Checklist

Before any real traffic is sent:

- [ ] Image tag in `litellm-deploy.yml` is pinned to a SHA or verified stable tag
- [ ] Secret `litellm-secrets` exists in namespace `llm-gateway` (created via Ansible `k8s-secrets`)
- [ ] Postgres user `litellm` and database `litellm` exist on jaguar
- [ ] No Ingress or public DNS record exists for this service
- [ ] Validation steps 1–4 above pass

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ImagePullBackOff` | Tag placeholder or missing digest | Pin a real tag/SHA in `litellm-deploy.yml` |
| `CrashLoopBackOff` | Missing `litellm-secrets` | Bootstrap secrets via Ansible k8s-secrets role |
| `Connection refused` on health | Container still starting | Wait for startup probe (max ~150s) |
| `401 Unauthorized` | Wrong master key | Verify `LITELLM_MASTER_KEY` in secret |
| Upstream 401 | Provider key is placeholder | Insert real key into pass and re-run Ansible |
| DB connection error | Postgres user/db missing on jaguar | Run Ansible DB provisioning for `litellm` |
