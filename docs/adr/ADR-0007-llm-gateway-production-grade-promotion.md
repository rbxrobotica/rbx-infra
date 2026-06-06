# ADR-0007: Promote llm-gateway from experimental LiteLLM to a production-grade LLM data plane

**Status**: Proposed

**Date**: 2026-06-06

**Governance anchors**:
[Thalamus ADR-0001](https://github.com/rbxrobotica/thalamus/blob/main/docs/adr/ADR-0001-thalamus-as-semantic-control-layer.md) (Thalamus as semantic control layer),
[ADR-0008](https://github.com/rbxrobotica/rbx-governance/blob/main/docs/adr/ADR-0008-agentic-mcp-governance-and-internal-domain-mcps.md) (Agentic MCP Governance — Agentgateway as recommended data plane),
[ADR-0006](ADR-0006-secrets-and-rate-limiting-ownership.md) (Secrets and rate-limiting ownership).

---

## Context

`apps/prod/llm-gateway/` runs a **LiteLLM** proxy in the production cluster
(namespace `llm-gateway`, ClusterIP only). As of 2026-06-06 it is the backend
that `rbx-market-briefing` — the **first paid product** ([ADR-0009, BTC briefing,
R$39/mês](https://github.com/rbxrobotica/rbx-governance)) — calls for its `--llm`
flight-plan generation.

It works (Groq / `llama-3.3-70b-versatile` responds), but it is **explicitly
experimental and interim**, not a production-grade gateway:

1. **Labelled experimental.** The deploy and config headers say "Experimental";
   model aliases are `groq-test`, `glm-test`, `kimi-moonshot-test`. The
   `README.md` marks it "Candidate… Not production-critical… safe to scale to zero".
2. **Running DB-less (degraded).** The DB-backed path is blocked by a LiteLLM
   v1.83.14 (wolfi/chainguard base) Prisma **runtime query-engine** failure
   (`NotConnectedError`, identical on `litellm` and `litellm-database` images).
   To bring the gateway up at all, it currently runs **without `DATABASE_URL`**
   (see the DB-less change in `litellm-deploy.yml` / `litellm-config.yml`). That
   removes: **virtual keys, per-product budgets, spend tracking, per-key rate
   limits.** All clients share the **master key**.
3. **No control plane in front.** Per Thalamus ADR-0001, the gateway is a
   *data plane*; Thalamus is the *control plane* (policy, audit, evaluation,
   risk classification, pre/post-call validation). Today consumers call LiteLLM
   **directly** ("Phase A" of the Thalamus migration path) — there is **no
   governance, audit, or policy** on LLM traffic.

For a revenue product this gap should be explicit and tracked, not implicit.

## Decision

1. **Record the debt.** The current `llm-gateway` is accepted **only as a
   Phase-A, non-governed, interim backend** suitable for bootstrapping and
   validating products. No paid product may treat it as production-grade
   infrastructure until the exit criteria below are met.

2. **Definition of "production-grade" (exit criteria).** The gateway is promoted
   when ALL hold:
   - **DB-backed identity**: per-product **virtual keys** (not the shared master
     key), with **budgets**, **spend tracking**, and **per-key rate limits**,
     backed by Postgres on jaguar (per ADR-0006 / Postgres-external rule).
   - **Governed**: LLM traffic flows **through Thalamus** (`BackendPort`) so
     policy, audit, risk classification, and pre/post-call validation apply —
     i.e. Phase C or later of the Thalamus migration, not direct calls.
   - **Stable image**: a pinned image whose Prisma runtime engine actually starts
     (no `NotConnectedError`), restoring `DATABASE_URL` cleanly.
   - **Operational**: documented key rotation, defined HA/SLO posture, and
     production model aliases (drop the `-test` suffix).

3. **Two candidate target architectures** (the A-vs-B choice is deferred to a
   follow-up ADR, but recorded here):
   - **A — Harden LiteLLM**: pin a non-wolfi/debian-based LiteLLM image (or fix
     the Prisma engine), re-add `DATABASE_URL` + `LITELLM_SALT_KEY` + config
     `database_url`, mint per-product virtual keys, then place Thalamus in front.
   - **B — Agentgateway** (solo.io) as the unified data plane (MCP + A2A + LLM)
     under Thalamus, per ADR-0008. Strategic target; larger effort (no deployment
     exists yet). LiteLLM is the interim that Agentgateway eventually replaces.

4. **Track it** as a roadmap item in `apps/prod/llm-gateway/README.md`.

## Consequences

- **While interim (now)**: acceptable for Phase-A product validation. Risks made
  explicit: shared master key (blast radius), no per-product budget/cost ceiling,
  no spend attribution, no rate limiting, no governance/audit on LLM traffic.
  `rbx-market-briefing`'s `rbx-market-briefing-llm` secret therefore holds the
  **master key**, not a virtual key — to be swapped when virtual keys return.
- **On promotion**: the chosen target (A or B) becomes a follow-up ADR; this ADR
  is superseded/closed when the exit criteria are met.
- **If not promoted**: paid products remain exposed to the interim risks above;
  this ADR exists so that is a conscious, tracked choice rather than an accident.

## Related

- `apps/prod/llm-gateway/README.md` — Roadmap: Promotion to production-grade.
- Thalamus `docs/02-architecture/agentgateway-and-data-plane.md` — migration phases A→D.
