#!/usr/bin/env bash
# dev-sandbox.sh — Ephemeral k3s dev sandbox for Robson PostgreSQL integration tests
#
# Usage:
#   dev-sandbox.sh create robson-pg [--user <user>] [--id <id>]
#   dev-sandbox.sh test   robson-pg --namespace <ns> --robson-path <path> [--port <local-port>]
#   dev-sandbox.sh url    robson-pg --namespace <ns> [--port <local-port>]
#   dev-sandbox.sh logs   robson-pg --namespace <ns>
#   dev-sandbox.sh status robson-pg --namespace <ns>
#   dev-sandbox.sh destroy robson-pg --namespace <ns>
#   dev-sandbox.sh list
#
# Environment:
#   SANDBOX_MANIFEST_DIR  — directory containing postgres.yml
#                           (default: <script-dir>/../apps/dev-sandboxes/robson-pg-recovery)
#
# Design:
#   - Namespace-per-sandbox: dev-robson-<user>-<id> or agent-robson-<id>
#   - Ephemeral PostgreSQL (emptyDir, no PVC)
#   - port-forward to expose PG locally for sqlx::test
#   - sqlx::test creates/drops per-test DBs automatically (CREATEDB granted in init.sql)
#
# Agent quick-start:
#   NS=$(dev-sandbox.sh create robson-pg --user codex | grep "Namespace:" | awk '{print $2}')
#   DATABASE_URL=$(dev-sandbox.sh url robson-pg --namespace "$NS")
#   DATABASE_URL="$DATABASE_URL" cargo test -p robson-projector --test integration_test -- --ignored
#   dev-sandbox.sh destroy robson-pg --namespace "$NS"

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST_DIR="${SANDBOX_MANIFEST_DIR:-${SCRIPT_DIR}/../apps/dev-sandboxes/robson-pg-recovery}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

info()    { echo -e "${BLUE}==>${NC} $*"; }
success() { echo -e "${GREEN}✓${NC} $*"; }
warn()    { echo -e "${YELLOW}⚠${NC} $*"; }
fail()    { echo -e "${RED}✗${NC} $*" >&2; }

PF_PID_FILE="/tmp/dev-sandbox-pf.pid"

# ─── helpers ──────────────────────────────────────────────────────────────────

_require_kubectl() {
    if ! command -v kubectl &>/dev/null; then
        fail "kubectl not found in PATH"
        exit 1
    fi
}

_require_namespace() {
    if [[ -z "${NAMESPACE:-}" ]]; then
        fail "--namespace is required for this command"
        echo "  Hint: use 'dev-sandbox.sh create robson-pg' to create a sandbox and get its namespace."
        exit 1
    fi
}

_wait_for_postgres() {
    local ns="$1"
    local timeout="${2:-120}"
    info "Waiting for PostgreSQL to be ready (timeout: ${timeout}s)..."
    kubectl rollout status deployment/robson-pg-sandbox -n "$ns" --timeout="${timeout}s"
    success "PostgreSQL deployment is ready"

    # Extra wait for pg_isready (readinessProbe may lag a few seconds)
    local count=0
    until kubectl exec -n "$ns" \
        "$(kubectl get pod -n "$ns" -l app=robson-pg-sandbox -o jsonpath='{.items[0].metadata.name}')" \
        -- pg_isready -U robson -d robson_v2 &>/dev/null; do
        count=$((count + 1))
        if [[ $count -gt 30 ]]; then
            fail "PostgreSQL did not pass pg_isready after ${count} attempts"
            exit 1
        fi
        sleep 2
    done
    success "PostgreSQL is accepting connections"
}

_start_port_forward() {
    local ns="$1"
    local local_port="$2"
    local pid_file="${PF_PID_FILE}.${ns}"

    # Kill existing port-forward for this namespace
    if [[ -f "$pid_file" ]]; then
        kill "$(cat "$pid_file")" 2>/dev/null || true
        rm -f "$pid_file"
    fi

    info "Starting port-forward localhost:${local_port} → robson-pg-sandbox:5432 in namespace ${ns}"
    kubectl port-forward -n "$ns" svc/robson-pg-sandbox "${local_port}:5432" &>/dev/null &
    echo $! > "$pid_file"

    # Give port-forward a moment to bind
    sleep 2

    if ! kill -0 "$(cat "$pid_file")" 2>/dev/null; then
        fail "Port-forward process died immediately — port ${local_port} may be in use"
        exit 1
    fi
    success "Port-forward active (PID $(cat "$pid_file"))"
}

_stop_port_forward() {
    local ns="$1"
    local pid_file="${PF_PID_FILE}.${ns}"
    if [[ -f "$pid_file" ]]; then
        kill "$(cat "$pid_file")" 2>/dev/null || true
        rm -f "$pid_file"
        info "Port-forward stopped"
    fi
}

# ─── commands ─────────────────────────────────────────────────────────────────

cmd_create() {
    local profile="$1"; shift

    local owner="user"
    local id
    id="$(date +%s | tail -c 6)"
    local user="${USER:-agent}"

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --user)    user="$2";  shift 2 ;;
            --id)      id="$2";    shift 2 ;;
            --agent)   owner="agent"; shift ;;
            *) fail "Unknown option: $1"; exit 1 ;;
        esac
    done

    if [[ "$owner" == "agent" ]]; then
        local ns="agent-robson-${id}"
    else
        local ns="dev-robson-${user}-${id}"
    fi

    _require_kubectl

    echo ""
    info "Creating dev sandbox: ${ns}"
    echo ""

    # Create namespace with labels
    kubectl create namespace "$ns" --dry-run=client -o yaml | \
        kubectl apply -f -
    kubectl label namespace "$ns" \
        rbx.io/env=dev-sandbox \
        rbx.io/app=robson \
        rbx.io/owner="${owner}" \
        rbx.io/ttl=24h \
        --overwrite

    # Apply manifests
    kubectl apply -n "$ns" -f "${MANIFEST_DIR}/postgres.yml"

    # Wait for readiness
    _wait_for_postgres "$ns"

    echo ""
    success "Sandbox created successfully"
    echo ""
    echo "  Namespace: ${ns}"
    echo "  Profile  : ${profile}"
    echo ""
    echo "  Next steps:"
    echo "    # Get DATABASE_URL (starts port-forward):"
    echo "    DATABASE_URL=\$(${BASH_SOURCE[0]} url ${profile} --namespace ${ns})"
    echo ""
    echo "    # Run Robson projector integration tests:"
    echo "    DATABASE_URL=\"\$DATABASE_URL\" cargo test -p robson-projector --test integration_test -- --ignored"
    echo ""
    echo "    # Or use the test command directly:"
    echo "    ${BASH_SOURCE[0]} test ${profile} --namespace ${ns} --robson-path /path/to/robson"
    echo ""
    echo "    # Destroy when done:"
    echo "    ${BASH_SOURCE[0]} destroy ${profile} --namespace ${ns}"
    echo ""
}

cmd_url() {
    local _profile="$1"; shift

    local local_port="15432"

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --namespace|-n) NAMESPACE="$2"; shift 2 ;;
            --port)         local_port="$2"; shift 2 ;;
            *) fail "Unknown option: $1"; exit 1 ;;
        esac
    done

    _require_kubectl
    _require_namespace

    _start_port_forward "$NAMESPACE" "$local_port"

    # Print DATABASE_URL — this is what agents capture
    echo "postgresql://robson:robson_dev@localhost:${local_port}/robson_v2"
}

cmd_test() {
    local _profile="$1"; shift

    local robson_path=""
    local local_port="15432"

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --namespace|-n)  NAMESPACE="$2";    shift 2 ;;
            --robson-path)   robson_path="$2";  shift 2 ;;
            --port)          local_port="$2";   shift 2 ;;
            *) fail "Unknown option: $1"; exit 1 ;;
        esac
    done

    _require_kubectl
    _require_namespace

    if [[ -z "$robson_path" ]]; then
        # Try to find robson repo relative to this script
        local candidate
        candidate="$(cd "${SCRIPT_DIR}/../.." && ls -d robson 2>/dev/null || true)"
        if [[ -d "${SCRIPT_DIR}/../../robson" ]]; then
            robson_path="${SCRIPT_DIR}/../../robson"
        else
            fail "--robson-path is required (path to the robson repository)"
            echo "  Example: --robson-path /home/psyctl/apps/robson"
            exit 1
        fi
    fi

    local v2_path="${robson_path}/v2"
    if [[ ! -d "$v2_path" ]]; then
        fail "v2/ not found at: ${v2_path}"
        exit 1
    fi

    _start_port_forward "$NAMESPACE" "$local_port"

    local db_url="postgresql://robson:robson_dev@localhost:${local_port}/robson_v2"

    echo ""
    info "Running robson-projector integration tests"
    info "  Namespace   : ${NAMESPACE}"
    info "  DATABASE_URL: ${db_url}"
    info "  Robson path : ${v2_path}"
    echo ""
    warn "sqlx::test creates temporary databases on the server — expected, safe, ephemeral."
    echo ""

    (
        cd "$v2_path"
        DATABASE_URL="$db_url" \
        cargo test -p robson-projector --test integration_test -- --ignored
    )

    local exit_code=$?

    _stop_port_forward "$NAMESPACE"

    if [[ $exit_code -eq 0 ]]; then
        success "All integration tests passed"
    else
        fail "Integration tests failed (exit code: ${exit_code})"
        exit $exit_code
    fi
}

cmd_logs() {
    local _profile="$1"; shift

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --namespace|-n) NAMESPACE="$2"; shift 2 ;;
            --follow|-f)    FOLLOW="--follow"; shift ;;
            *) fail "Unknown option: $1"; exit 1 ;;
        esac
    done

    _require_kubectl
    _require_namespace

    local pod
    pod="$(kubectl get pod -n "$NAMESPACE" -l app=robson-pg-sandbox -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"

    if [[ -z "$pod" ]]; then
        fail "No robson-pg-sandbox pod found in namespace: ${NAMESPACE}"
        exit 1
    fi

    kubectl logs -n "$NAMESPACE" "$pod" ${FOLLOW:-}
}

cmd_status() {
    local _profile="$1"; shift

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --namespace|-n) NAMESPACE="$2"; shift 2 ;;
            *) fail "Unknown option: $1"; exit 1 ;;
        esac
    done

    _require_kubectl
    _require_namespace

    echo ""
    info "Sandbox status: ${NAMESPACE}"
    echo ""
    kubectl get pods,svc -n "$NAMESPACE" -l rbx.io/app=robson
    echo ""
}

cmd_destroy() {
    local _profile="$1"; shift

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --namespace|-n) NAMESPACE="$2"; shift 2 ;;
            *) fail "Unknown option: $1"; exit 1 ;;
        esac
    done

    _require_kubectl
    _require_namespace

    _stop_port_forward "$NAMESPACE"

    info "Destroying sandbox namespace: ${NAMESPACE}"
    kubectl delete namespace "$NAMESPACE" --ignore-not-found=true
    success "Sandbox destroyed: ${NAMESPACE}"
}

cmd_list() {
    _require_kubectl
    echo ""
    info "Active dev sandboxes:"
    echo ""
    kubectl get namespaces -l rbx.io/env=dev-sandbox -o \
        custom-columns="NAMESPACE:.metadata.name,OWNER:.metadata.labels['rbx\.io/owner'],APP:.metadata.labels['rbx\.io/app'],TTL:.metadata.labels['rbx\.io/ttl'],AGE:.metadata.creationTimestamp" \
        2>/dev/null || echo "  (none)"
    echo ""
}

# ─── dispatch ─────────────────────────────────────────────────────────────────

COMMAND="${1:-}"
PROFILE="${2:-}"

case "$COMMAND" in
    create)  cmd_create  "$PROFILE" "${@:3}" ;;
    url)     cmd_url     "$PROFILE" "${@:3}" ;;
    test)    cmd_test    "$PROFILE" "${@:3}" ;;
    logs)    cmd_logs    "$PROFILE" "${@:3}" ;;
    status)  cmd_status  "$PROFILE" "${@:3}" ;;
    destroy) cmd_destroy "$PROFILE" "${@:3}" ;;
    list)    cmd_list ;;
    "")
        echo "Usage: dev-sandbox.sh <command> <profile> [options]"
        echo ""
        echo "Commands:"
        echo "  create  robson-pg [--user <user>] [--id <id>] [--agent]"
        echo "  url     robson-pg --namespace <ns> [--port <port>]"
        echo "  test    robson-pg --namespace <ns> --robson-path <path> [--port <port>]"
        echo "  logs    robson-pg --namespace <ns> [--follow]"
        echo "  status  robson-pg --namespace <ns>"
        echo "  destroy robson-pg --namespace <ns>"
        echo "  list"
        echo ""
        echo "Agent quick-start:"
        echo "  NS=\$(dev-sandbox.sh create robson-pg --agent | grep 'Namespace:' | awk '{print \$2}')"
        echo "  DATABASE_URL=\$(dev-sandbox.sh url robson-pg --namespace \"\$NS\")"
        echo "  DATABASE_URL=\"\$DATABASE_URL\" cargo test -p robson-projector --test integration_test -- --ignored"
        echo "  dev-sandbox.sh destroy robson-pg --namespace \"\$NS\""
        exit 0
        ;;
    *)
        fail "Unknown command: ${COMMAND}"
        exit 1
        ;;
esac
