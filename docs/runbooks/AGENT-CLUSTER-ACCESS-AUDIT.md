# Runbook · Corbetti Agent Cluster Access Audit

**Authority**: ADR-0500 Amendment 2026-07 · rbx-security ai-agent-security-policy §8
Exception AGENT-KUBE-001 · threat-model T7/T10/T13.
**Cadence**: review weekly (or after any agent-driven incident); rotate tokens quarterly
or on incident.

## What the agents can do

The 4 Corbetti agent ServiceAccounts (`corbetti-claude`/`codex`/`kimi`/`glm` in namespace
`corbetti-agents`) hold scoped WRITE (`create`/`update`/`patch`/`delete` + read/exec/logs/
port-forward) to **8 namespaces**: robson, rbx-commerce, rbx-maestro, rbx-comms, truthmetal,
eden, llm-gateway, rbx-observability.

Fences (defense-in-depth):
- **Crown-jewel namespaces** (rbx-ia-br, argocd, kube-system, monitoring, default) are
  fenced out by RBAC absence (no RoleBinding) + `ValidatingAdmissionPolicy` VAP-2.
- **Destructive ops on Secrets/PVC and RBAC writes** are denied by VAP-1, even where the
  Role verbs would allow them (anti-escalation).
- **selfHeal+prune** reverts ad-hoc `kubectl apply` to ArgoCD-managed kinds within the sync
  window; persistent mutation still goes through PR → review → ArgoCD.

## Per-operation authorization (policy §2)

Every MUTATING kubectl action by an agent requires explicit human authorization in the
current turn. The kubeconfigs ENABLE the action; they do NOT AUTHORIZE it. Cryptographic
enforcement is deferred to the ADR-0500 Phase 3 pull-based runner; until then this audit
+ the VAP guardrails + namespace fence are the controls.

## Audit log

- Path on all server nodes (tiger, altaica, sumatrae): `/var/lib/rancher/k3s/server/logs/audit.log`
- Level: Metadata · 30-day retention. **Writes** (create/update/patch/delete, including
  `exec` and `port-forward` which are CREATE/CONNECT) are logged and attributed to the SA.
  Pure reads (get/list/watch) are at level None and are NOT logged.

## Review queries (ssh a server node)

```bash
AUDIT=/var/lib/rancher/k3s/server/logs/audit.log

# 1) Any WRITE by the 4 agent SAs (24h). This is the primary signal.
for u in corbetti-claude corbetti-codex corbetti-kimi corbetti-glm; do
  grep "system:serviceaccount:corbetti-agents:$u" "$AUDIT" \
    | jq -r 'select(.verb|test("create|update|patch|delete")) |
             "\(.requestReceivedTimestamp) \(.verb) \(.user.username) \(.objectRef.namespace)/\(.objectRef.resource)/\(.objectRef.name)"'
done

# 2) Fence check — any reach into crown-jewel namespaces. EMPTY = OK; any hit = incident.
grep -E 'system:serviceaccount:corbetti-agents:' "$AUDIT" \
  | jq -r 'select(.objectRef.namespace|test("rbx-ia-br|argocd|kube-system|monitoring|default"))?'

# 3) VAP denials — confirms the guardrail is firing (expected when an agent attempts a
#    blocked op). 403/Forbidden from admission.
grep -E 'system:serviceaccount:corbetti-agents:' "$AUDIT" \
  | jq -r 'select(.annotations."authorization.k8s.io/decision"=="forbid"
                or .responseStatus.code==403)'
```

## Token rotation (quarterly or on incident)

Long-lived ServiceAccount tokens back the kubeconfigs. Rotate one agent at a time:

```bash
# On the operator workstation (kubeconfig = admin, from pass rbx/cluster/kubeconfig):
AGENT=claude   # one of claude|codex|kimi|glm
K=~/.kube/config-rbx
NS=corbetti-agents

# 1. Delete the token Secret (invalidates the long-lived token it backs).
kubectl --kubeconfig "$K" -n "$NS" delete secret "corbetti-${AGENT}-token"

# 2. Re-create it (token controller repopulates data.token / data.ca.crt).
kubectl --kubeconfig "$K" apply -f - <<EOF
apiVersion: v1
kind: Secret
type: kubernetes.io/service-account-token
metadata:
  name: corbetti-${AGENT}-token
  namespace: ${NS}
  annotations:
    kubernetes.io/service-account.name: corbetti-${AGENT}
EOF

# 3. Re-assemble the kubeconfig (see rbx-agent-tokens role) and store in pass.
#    pass insert --multiline rbx/corbetti/${AGENT}-kubeconfig

# 4. Re-deliver to Corbetti: bash bootstrap/scripts/init-vault-from-pass.sh
#    + ansible-playbook (agent-workbench role) against the agent_workbench group.

# 5. Confirm the OLD token is rejected.
kubectl --kubeconfig <old-kubeconfig> get ns   # expect 401 Unauthorized
```

## Incident response

If the audit shows an unexpected agent write, a crown-jewel hit, or a suspected token
compromise: treat as a security incident. Immediate containment — revoke the token
(step 1 above for the implicated agent), then follow the rbx-security incident process.
