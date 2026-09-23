# Decision-to-action E2E (claude_code_design profile)

Owner-runnable, local, end-to-end exercise of the chain

```
FlightDeck (standing authorization + policy dispatch decision)
  -> Public Presence (source event, materialization, creative job)
  -> Maestro (admission, request-bound activation, lease)
  -> Corbetti (real executor scripts, fake Claude CLI, fake gh)
  -> Maestro (ExecutionManifest + DeliveryManifest, result projection)
  -> Public Presence (owner-side import, QA mapping, autonomous approval, planned publication)
```

Real code everywhere except the external edges:

| Component | Real | Fake |
|---|---|---|
| Flight Deck | service layer, migrations, stages, policy dispatch | run through a Bun driver instead of the HTTP worker (`flightdeck-drive.ts`) |
| Maestro | `cmd/api` with `AGENT_LOOP_AUTH=off`, Flight Deck keys, `MAESTRO_ENVIRONMENT=test` | |
| Public Presence | `cmd/api serve`, doctrine from `identities/`, fs asset store, outbox worker **off** | GitHub contents API (`fakes/github-contents.py` serving a local bare repo) |
| Corbetti | `rbx-mission-executor.sh`, adapter, path policy, verify, branch, PR-before-delivery | `claude` CLI (`fakes/claude` writes a compliant creative mission), `gh` (`fakes/gh` returns a PR URL), target repo (`rbxrobotica/rbx-creatives` stand-in with `fakes/verify.ts`) |

Nothing is published. The run asserts:

1. the intent receives a **policy** dispatch decision right after Maestro admission (no human per-request approval) and only then is materialized and activated;
2. the executor delivers with `capabilities.design.mode == "degraded"` and **no** `design` attestation (no Claude Design on the host);
3. reconcile brings the intent to `TERMINAL delivered`;
4. Public Presence imported the artifacts (hash-verified), created a version, approved it under the standing authorization, moved the item to `SCHEDULED` and planned exactly one publication (outbox worker disabled, so no remote effect).

## Run

```bash
E2E_FD_DIR=~/rbx/rbx-flightdeck E2E_PP_DIR=~/rbx/rbx-public-presence \
E2E_MAESTRO_DIR=~/rbx/rbx-maestro bash e2e/decision-to-action/run.sh
```

Prerequisites: go, bun, psql, python3, jq, git, curl, podman. The script creates or
starts the three disposable Postgres containers the repositories' own test suites
use (`flightdeck-db-test` 5434, `presence-db-test` 5435, `maestro-db-test` 5436) and
**resets their schemas**. Do not run those test suites concurrently.

Artifacts of a run stay under `/tmp/d2a-e2e.*` (service logs, stage outputs,
runner manifests, the fake origin repository).

## What this E2E does not prove

- A real Claude Code or Claude Design execution (the CLI is fake; the capability
  probe honestly reports the host has no headless Claude Design).
- A real publication: the outbox worker is off and channel credentials are absent.
- Semantic Brand QA: Public Presence maps the repository's deterministic QA
  checks; it does not re-execute them.
- Production credentials, secrets or activation: none are used.
