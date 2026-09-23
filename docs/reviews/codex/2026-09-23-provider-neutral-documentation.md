# Codex review: provider-neutral Corbetti documentation

Date: 2026-09-23
Model: `gpt-5.6-sol`, reasoning effort `medium`

## Scope

Reviewed executor adapters, capability probes, source pinning, compatibility
fallbacks and terminal manifest documentation against the Corbetti scripts.

## Resolved findings

- Qualified exact source pinning as an executable-ingress guarantee and
  documented the legacy base-branch compatibility path.
- Documented the legacy `claude-haiku` default when `executor` is absent while
  preserving explicit executor authority for current ingress.
- Kept design-surface capability evidence separate from coding executor names.

## Validation and verdict

- Provider-neutral runner contract: passed.
- Capability-probe contract: passed.
- `git diff --check`: passed.
- Final Codex review: no actionable findings.
