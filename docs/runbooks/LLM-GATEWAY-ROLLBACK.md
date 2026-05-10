# LLM Gateway Rollback Runbook

**Audience:** RBX operators rolling back or removing the LiteLLM experiment.
**Prerequisites:** `kubectl` access, `KUBECONFIG=~/.kube/config-rbx`, ArgoCD admin access.

> **Warning:** LiteLLM Proxy is **experimental**. The safest rollback is to remove the
> ArgoCD Application or disable its sync. There is no production traffic at risk.

---

## Quick Rollback (Disable Sync, Keep Manifests)

If the proxy is misbehaving but you want to preserve the manifests for debugging:

```bash
export KUBECONFIG=~/.kube/config-rbx

# Set ArgoCD Application to manual sync
argocd app set llm-gateway --sync-policy none

# Scale deployment to zero (instant traffic stop)
kubectl scale deployment litellm -n llm-gateway --replicas=0

# Verify no pods are running
kubectl get pods -n llm-gateway
```

To restore:

```bash
argocd app set llm-gateway --sync-policy automated --auto-prune --self-heal
```

ArgoCD will recreate the pod automatically.

---

## Full Removal (Delete Application and Namespace)

If the experiment is concluded and should be fully removed:

### Step 1: Disable ArgoCD auto-sync

```bash
argocd app set llm-gateway --sync-policy none
```

### Step 2: Delete the ArgoCD Application resource

```bash
kubectl delete application llm-gateway -n argocd
```

> Deleting the Application object does **not** delete the namespace or its resources unless the finalizer `resources-finalizer.argocd.argoproj.io` is present. In this repo, the finalizer is enabled, so ArgoCD will cascade-delete all tracked resources.

### Step 3: Verify cleanup

```bash
# Namespace should be gone
kubectl get namespace llm-gateway

# If namespace persists (e.g., finalizer stuck), force delete
kubectl delete namespace llm-gateway --wait=false
kubectl get namespace llm-gateway -o json | \
  jq 'del(.spec.finalizers)' | \
  kubectl replace --raw "/api/v1/namespaces/llm-gateway/finalize" -f -
```

### Step 4: Remove from Git (optional, post-experiment)

```bash
# Delete manifests
git rm -r apps/prod/llm-gateway/
git rm core/namespaces/llm-gateway.yml
git rm gitops/app-of-apps/llm-gateway.yml

# Remove namespace from AppProject
git checkout gitops/projects/rbx-applications.yaml
# Edit manually to remove the llm-gateway destination
```

Open a PR titled `chore: remove llm-gateway experiment`.

---

## Database Cleanup

The LiteLLM Proxy stores virtual keys and spend data in Postgres.
If the experiment is fully concluded, drop the database on **jaguar**:

```bash
# Run on jaguar (or via Ansible)
sudo -u postgres psql -c "DROP DATABASE IF EXISTS litellm;"
sudo -u postgres psql -c "DROP USER IF EXISTS litellm;"
```

> **Warning:** This is irreversible. Ensure no other service shares the `litellm` database.

---

## Secret Cleanup

Kubernetes Secret `litellm-secrets` is cascade-deleted with the namespace.
To also remove from `pass`:

```bash
pass rm -r rbx/llm-gateway
```

---

## Incident Log Template

If rollback was triggered by an incident, append to `docs/incidents/`:

```markdown
## YYYY-MM-DD — LLM Gateway Rollback

- **Trigger:** <symptom>
- **Action:** <which rollback path above>
- **Impact:** <none / internal-only / ...>
- **Root cause:** <if known>
- **Follow-up:** <ticket or ADR reference>
```
