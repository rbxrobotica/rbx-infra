#!/usr/bin/env bash
# mint-corbetti-tokens.sh — mint long-lived ServiceAccount tokens for the 4 Corbetti
# agent SAs, assemble per-agent kubeconfigs, and store them in pass.
# ADR-0500 Amendment 2026-07 / rbx-security AGENT-KUBE-001.
#
# Run on the OPERATOR WORKSTATION (needs admin kubeconfig + an unlocked pass store).
# Idempotent: re-running re-mints (deletes old token Secret, invalidating the old token).
#
#   KUBECONFIG=~/.kube/config-rbx bash bootstrap/scripts/mint-corbetti-tokens.sh
#
# Token Secrets are minted OUT-OF-BAND (not GitOps) so ArgoCD selfHeal never fights the
# token controller. ArgoCD does not manage these Secrets (only ns/SA/Role/RoleBinding),
# so prune does not touch them. Rotation runbook: docs/runbooks/AGENT-CLUSTER-ACCESS-AUDIT.md.
#
# NEVER prints token values. Kubeconfigs go straight pass -> (encrypted), never stdout.
set -euo pipefail

: "${KUBECONFIG:=$HOME/.kube/config-rbx}"
AGENTS=(claude codex kimi glm)
NS=corbetti-agents
API_SERVER="https://158.220.116.31:6443"
CLUSTER_NAME="rbx-prod"

[ -f "$KUBECONFIG" ] || { echo "ERR: kubeconfig not found: $KUBECONFIG" >&2; exit 1; }
kubectl --kubeconfig "$KUBECONFIG" get ns "$NS" >/dev/null 2>&1 || { echo "ERR: namespace $NS not found (ArgoCD synced rbx-agent-access?)" >&2; exit 1; }

for agent in "${AGENTS[@]}"; do
  sa="corbetti-${agent}"
  secret="${sa}-token"
  pass_path="rbx/corbetti/${agent}-kubeconfig"
  echo "==> ${sa}"

  # Delete any existing token Secret (idempotent re-mint; invalidates the prior token).
  kubectl --kubeconfig "$KUBECONFIG" delete secret "$secret" -n "$NS" --ignore-not-found >/dev/null

  # Create the token Secret.
  kubectl --kubeconfig "$KUBECONFIG" apply -f - >/dev/null <<EOF
apiVersion: v1
kind: Secret
type: kubernetes.io/service-account-token
metadata:
  name: ${secret}
  namespace: ${NS}
  annotations:
    kubernetes.io/service-account.name: ${sa}
EOF

  # Wait for the token controller to populate data.token + data.ca.crt.
  printf '   waiting for token controller...'
  populated=""
  for _ in $(seq 1 30); do
    if kubectl --kubeconfig "$KUBECONFIG" get secret "$secret" -n "$NS" -o jsonpath='{.data.token}' 2>/dev/null | grep -q .; then
      populated=1; break
    fi
    sleep 1
  done
  [ -n "$populated" ] || { echo " TIMEOUT"; echo "ERR: token not populated for $sa" >&2; exit 1; }
  echo " ok"

  token_b64=$(kubectl --kubeconfig "$KUBECONFIG" get secret "$secret" -n "$NS" -o jsonpath='{.data.token}')
  # CA: k3s does NOT populate data.ca.crt on legacy SA-token Secrets (it stays empty),
  # which breaks TLS verification. Use the canonical API CA from the kube-root-ca.crt
  # ConfigMap instead (same CA the operator kubeconfig carries; validates the server cert).
  ca_b64=$(kubectl  --kubeconfig "$KUBECONFIG" get cm kube-root-ca.crt -n "$NS" -o jsonpath='{.data.ca\.crt}' | base64 -w0)
  token=$(printf '%s' "$token_b64" | base64 -d)

  # Assemble kubeconfig (CA stays base64; token is the raw JWT).
  kc=$(cat <<EOF
apiVersion: v1
kind: Config
clusters:
  - name: ${CLUSTER_NAME}
    cluster:
      server: ${API_SERVER}
      certificate-authority-data: ${ca_b64}
contexts:
  - name: corbetti-${agent}
    context:
      cluster: ${CLUSTER_NAME}
      user: corbetti-${agent}
current-context: corbetti-${agent}
users:
  - name: corbetti-${agent}
    user:
      token: ${token}
EOF
)

  # Store in pass (force overwrite, multiline). Never echo the value.
  if printf '%s' "$kc" | pass insert -m -f "$pass_path" >/dev/null 2>&1; then
    echo "   stored pass:${pass_path}"
  else
    echo "ERR: pass insert failed for ${pass_path} (store unlocked?)" >&2; exit 1
  fi
done

echo "==> done. 4 kubeconfigs minted + stored in pass (rbx/corbetti/<agent>-kubeconfig)."
