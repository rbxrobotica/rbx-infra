#!/usr/bin/env bash
set -euo pipefail

# Operator-only preparation for the fail-closed sandbox rollout. This updates
# one source Secret property; it never reads or logs any other Secret value.
if [[ $# -ne 1 || ! -f "$1" ]]; then
  echo 'usage: patch-commerce-sandbox-comms-inert-url.sh /path/to/reviewed-kubeconfig' >&2
  exit 2
fi

kubeconfig_file=$1
namespace=rbx-ia-br
name=rbx-commerce-sandbox-secrets

kubectl --kubeconfig "$kubeconfig_file" --namespace "$namespace" \
  get secret "$name" >/dev/null

python3 - <<'PY' \
  | kubectl --kubeconfig "$kubeconfig_file" --namespace "$namespace" \
      patch secret "$name" --type merge --patch-file=/dev/stdin
import base64
import json

url = "http://127.0.0.1:1"
print(json.dumps({"data": {"COMMS_API_URL": base64.b64encode(url.encode()).decode()}}))
PY

kubectl --kubeconfig "$kubeconfig_file" --namespace "$namespace" \
  get secret "$name" -o 'jsonpath={.data.COMMS_API_URL}' \
  | python3 -c 'import base64,sys; sys.exit(0 if base64.b64decode(sys.stdin.read()).decode() == "http://127.0.0.1:1" else 1)'
