# Corbetti claude_code_design slice and decision-to-action E2E: Codex review gate

Date: 2026-09-23
Reviewer: Codex CLI 0.147.0, model gpt-5.6-sol, reasoning effort medium, read-only sandbox
Scope: branch `feat/corbetti-claude-design-slice`: per-mission capability probe,
capability gate, `delivery.capabilities.design` evidence, `design` attestation only when
observed and attested from a staged regular blob, canonical
`schemas/capability-report.v1.schema.json`, contract tests (9 missions), runbook and
`docs/infra/CORBETTI-EXECUTION.md`, and the four-repository E2E under
`e2e/decision-to-action/`.

## Result

Round 3: no findings. VERDICT: APPROVE.

## Findings resolved

### Round 1 (REQUEST_CHANGES)
1. blocking: `e2e/decision-to-action/fakes/claude` did not answer the adapter's `--version`
   and `--help` probes, mistook grep output for a mission directory and wrote into the
   caller's tree. The fake now answers probes without touching the tree, refuses
   option-looking prompts and constrains the mission directory pattern.
2. blocking: the attestation was validated through the working-tree path and followed
   symlinks. The executor now validates the staged Git object (`git ls-files -s` mode
   `100644`, content via `git show :path`) for both attestation and bundle; symlinks,
   executables and gitlinks are rejected. Mission 9 pins a staged symlink attestation.
3. should-fix: the harness wrote and deleted a driver file inside the Flight Deck
   checkout. The driver is now generated under the temporary work directory and imports
   Flight Deck sources by absolute path.
4. should-fix: the harness left database containers it started running. `ensure_db`
   records containers it created or started and cleanup stops only those.

### Round 2 (REQUEST_CHANGES)
1. blocking: leftover probe-generated directories from the round 1 bug were still present
   under `e2e/decision-to-action/`. Removed; the tree contains only intended files.

## Invariants reviewed
- The mission contract declares a capability; it is never proof it ran.
- A `design` attestation exists only when the probe observed a headless interface and the
  mission staged `design/attestation.json` with the same probe fingerprint; degraded mode
  emits none.
- `gh pr create` precedes `submit_delivery`; `rolling budget stop requested` appears
  exactly twice; no `rm -rf`; secrets never enter prompt, worktree or manifests.
- The E2E publishes nothing (outbox worker off), uses no secrets, resets only the
  disposable test databases and kills what it starts.

## Evidence
- Five contract tests PASS (`runner-e2e-contract.sh` with 9 missions); `bash -n`;
  `shellcheck -S warning` clean.
- Four-repository E2E PASSED locally on 2026-09-23 (policy dispatch, activation, Corbetti
  delivery in degraded design mode, Maestro projection, Public Presence import with
  verified hashes, QA mapping, autonomous approval, SCHEDULED publication).
