#!/usr/bin/env bash
set -euo pipefail

# Operator-only, narrowly scoped source Secret creation. This script does not
# generate a key, change a workload, or alter any existing Secret.
if [[ $# -ne 1 || ! -f "$1" ]]; then
  echo 'usage: create-commerce-consumption-key-source.sh /path/to/reviewed-kubeconfig' >&2
  exit 2
fi

KUBECONFIG_FILE=$1
SOURCE_PATH=rbx/commerce/consumption-service-key
SOURCE_SECRET=rbx-commerce-consumption-key
NAMESPACE=rbx-ia-br

kubectl --kubeconfig "$KUBECONFIG_FILE" get namespace "$NAMESPACE" >/dev/null
existing=$(kubectl --kubeconfig "$KUBECONFIG_FILE" --namespace "$NAMESPACE" \
  get secret "$SOURCE_SECRET" --ignore-not-found -o name)
if [[ -n "$existing" ]]; then
  echo 'source Secret already exists; inspect it before any rotation' >&2
  exit 1
fi

# The single key moves through stdin only. Never add shell tracing or print the
# generated JSON: it contains the base64-encoded credential.
pass show "$SOURCE_PATH" \
  | python3 -c 'import base64,json,sys
lines=sys.stdin.read().splitlines()
if len(lines)!=1 or len(lines[0])<32 or any(ch.isspace() for ch in lines[0]):
    sys.exit("pass entry must be one whitespace-free line of at least 32 characters")
print(json.dumps({"apiVersion":"v1","kind":"Secret","metadata":{"name":sys.argv[2],"namespace":sys.argv[1]},"type":"Opaque","data":{"COMMERCE_CONSUMPTION_SERVICE_KEY":base64.b64encode(lines[0].encode()).decode()}}))' "$NAMESPACE" "$SOURCE_SECRET" \
  | kubectl --kubeconfig "$KUBECONFIG_FILE" create -f -
