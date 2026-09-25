#!/usr/bin/env bash
set -euo pipefail

# Operator-only, narrowly scoped production Secret patch. Do not call the
# broad k8s-secrets role here: its rbx-contact-secrets task still omits the
# active Meta credentials and could disrupt Comms reconciliation.
if [[ $# -ne 1 || ! -f "$1" ]]; then
  echo 'usage: patch-commerce-comms-key.sh /path/to/reviewed-kubeconfig' >&2
  exit 2
fi

KUBECONFIG_FILE=$1
kubectl --kubeconfig "$KUBECONFIG_FILE" --namespace rbx-ia-br \
  get secret rbx-commerce-secrets >/dev/null

# Patch one data key only. The service key flows over stdin into kubectl, not
# into an argv value, shell tracing, a repository file, or command output.
pass show rbx/comms/service-api-key \
  | python3 -c 'import base64,json,sys; lines=sys.stdin.read().splitlines(); key=lines[0].strip() if lines else ""; sys.exit("empty Comms service key") if not key else None; print(json.dumps({"data":{"COMMS_SERVICE_API_KEY":base64.b64encode(key.encode()).decode()}}))' \
  | kubectl --kubeconfig "$KUBECONFIG_FILE" --namespace rbx-ia-br \
      patch secret rbx-commerce-secrets --type merge --patch-file=/dev/stdin
