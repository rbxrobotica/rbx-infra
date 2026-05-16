# Thalamus Control Plane Alignment (Infra Notes)

**Date**: 2026-05-16
**Type**: Analysis note (not an ADR, not a manifest change)
**Canonical source**: `thalamus-core`,
`docs/adr/ADR-0001-thalamus-as-semantic-control-layer.md`

## Why this note exists

Thalamus has been redefined as the **semantic control layer for AI traffic**.
This note records what that means for `rbx-infra` and captures the monitoring
stack verification done during the pivot. No production manifests were changed
by the pivot beyond doc-aligned annotations
(`core/namespaces/thalamus.yml` comment header,
`catalog/products.yml` description,
`README.md` / `CLAUDE.md` one-line descriptions).

## Control plane vs data plane in this cluster

```
Thalamus  (ns: thalamus)        = control plane
  - policy, context authorization, pre/post-call validation,
    audit, evaluation, routing decisions
  - NOT a gateway/proxy; no transport, no rate-limit mechanics

llm-gateway (ns: llm-gateway)   = data plane (experimental)
  - LiteLLM proxy, ClusterIP only, no Ingress, non-critical
  - connectivity/proxy/aliases; no policy/audit/eval
  - this is the kind of backend Thalamus reaches via BackendPort
```

Implications:

- The `thalamus` namespace hosts a control-plane service (decision/validation
  traffic), not a high-throughput proxy. Resource sizing should reflect that.
- The experimental LiteLLM in `apps/prod/llm-gateway` is a data-plane backend.
  Thalamus is the missing control plane above it. A `BackendPort` adapter
  targeting this LiteLLM deployment lets governance be adopted before
  Agentgateway is introduced.
- If Agentgateway is introduced later, it is another data-plane backend behind
  the same `BackendPort`. It is not Thalamus and Thalamus must not depend on
  Agentgateway types (invariant in ADR-0001).

## Monitoring stack: verified state (2026-05-16)

Verified by inspecting `platform/monitoring/`:

| Tool | Present | Where |
|------|---------|-------|
| Prometheus | Yes | `platform/monitoring/kube-prometheus-stack.yml` (Helm `kube-prometheus-stack` 67.4.0), 7d retention, scrapes `prometheus.io/scrape: "true"` pods |
| Grafana | Yes | same chart; `grafana.rbxsystems.ch`; Loki pre-wired as datasource |
| Alertmanager | Yes | same chart |
| node-exporter / kube-state-metrics | Yes | same chart |
| Loki | Yes | `platform/monitoring/loki.yml` |
| Promtail | Yes | `platform/monitoring/promtail.yml` |
| OpenTelemetry Collector | No | not deployed anywhere in `platform/` |
| Trace backend (Tempo/Jaeger) | No | not deployed |
| Langfuse | No | not deployed |

## Recommended infra follow-ups (not yet decided/owned)

These are recommendations for the infra owner, not commitments:

1. Deploy an OpenTelemetry Collector (OTLP, `monitoring` ns) so the
   `trace_id` already mandated by `rbx-harness/spec/protocol.md` and emitted by
   Thalamus can be collected end to end. Pair with a trace backend (Tempo fits
   the self-hosted, budget-conscious posture of ADR-11).
2. Evaluate Langfuse for LLM trace/evaluation. Constraint: Langfuse needs
   PostgreSQL, and PostgreSQL never runs in the production k3s cluster
   (`docs/infra/ARCHITECTURE.md`). A Langfuse deployment must use an external
   Postgres on a dedicated VPS, following the same in-namespace
   Service + explicit `Endpoints` pattern as `apps/prod/llm-gateway`.
3. `thalamus-server`, when it exists, should expose a Prometheus metrics
   endpoint and carry `prometheus.io/scrape` annotations so it is picked up
   automatically. Control-plane metrics to export: policy decision counts,
   validation failure counts, provider failure rates, pre/post-call latency,
   budget-exceeded and rate-limit events.

Separation reminder: Prometheus/Grafana are infrastructure metrics and
dashboards. They are not a replacement for Langfuse, which is the LLM-specific
trace and evaluation layer.

## References

- `thalamus-core/docs/adr/ADR-0001-thalamus-as-semantic-control-layer.md`
- `thalamus-core/docs/02-architecture/observability-and-evaluation.md`
- `thalamus-core/docs/02-architecture/agentgateway-and-data-plane.md`
- `apps/prod/llm-gateway/README.md`
- `platform/monitoring/kube-prometheus-stack.yml`
- `docs/infra/ARCHITECTURE.md` (Postgres-external constraint)
