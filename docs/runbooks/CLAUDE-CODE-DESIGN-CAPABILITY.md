# claude_code_design capability on Corbetti

Scope: what the `claude_code_design` creative adapter can and cannot do on an
`agent-workbench` host today, and how a caller learns that without guessing.

## The two halves of the adapter

`claude_code_design` names two capabilities, and they are not equally available.

| Half | Interface | State |
|---|---|---|
| Claude Code, headless executor | `claude --print --output-format stream-json` | **Available and probeable** |
| Claude Design | DesignSync, inside an interactive Claude Code session | **No headless interface** |

FlightDeck maps `claude_code_design` onto the `claude-sonnet` executor, so the
runner sees an ordinary Anthropic CLI executor. The Design half is not part of
that mapping and must never be implied by it.

## Why Claude Design has no headless path

The only interface to Claude Design is the `DesignSync` tool inside an
interactive Claude Code session. It authenticates through the **owner's
claude.ai login** (or a dedicated `/design-login` authorization), and its write
methods require a `finalize_plan` that a human reviews before any file is
written.

Using it from a Corbetti executor would require putting owner credentials into
the executor process. The mission contract forbids exactly that. So there is no
sanctioned direct path, and the probe reports `interface: "none"` rather than
degrading quietly.

This is a capability limit, not a bug to code around. Do not add a fallback that
drives a browser session against claude.ai to simulate it.

## The supported mode: export and import

Design participates through repository content, not through a live call:

1. The owner works in Claude Design interactively and exports a bundle.
2. The bundle is committed to the target repository (for `rbx-creatives`, under
   `design/exports/<YYYY-MM>/<format>/`, per its monthly cadence).
3. A mission consumes it as ordinary repository content, pinned by the admitted
   `source_commit` like everything else.

A delivery that consumed such a bundle records it in
`delivery.capabilities.design` as `mode: "degraded"` with the bundle's path and
hash. A delivery produced without any design bundle records `mode: "degraded"`
(when the contract permits it) or `mode: "unavailable"` (optional capability),
without bundle fields. A `delivery.design` attestation with `status: "used"` is
emitted only when a headless interface was observed **and** the executor left
matching evidence; see below. Nothing in the contract or the adapter name is
ever treated as proof that Design participated.

## Per-mission contract: `capabilities.design`

A mission contract may declare what it needs from Design:

```json
"capabilities": {
  "design": { "required": true, "degraded_mode": "repository_design_bundle" }
}
```

- `required: false` (or an absent object) means Design is optional.
- `degraded_mode` is `"repository_design_bundle"` or `null`.
- FlightDeck emits `required: true, degraded_mode: "repository_design_bundle"`
  for the `claude_code_design` adapter.

The executor (`rbx-mission-executor.sh`) runs the probe **per mission**, right
after `describe` succeeds and before any repository mutation, and stores the
report as `~/rbx/manifests/<mission>/capability.json`. A probe that fails is
refused like an unknown executor (exit 65, nothing submitted), because nothing
has been mutated yet.

### The degraded-mode gate

After the detached worktree exists (so the FailureManifest carries a valid
`base_commit`) the executor applies one rule:

| `required` | headless observed | `degraded_mode` | Outcome |
|---|---|---|---|
| false / absent | any | any | run normally; attestation only if `capabilities.design` is present |
| true | yes | any | run normally |
| true | no | `repository_design_bundle` | run in degraded mode (see below) |
| true | no | `null` | FailureManifest `phase: "capability"`, `stop_reason: "persistent_failure"`; no executor run, no branch |

In degraded mode the prompt gains a paragraph telling the executor that Claude
Design is unavailable on this host, that it must work from repository design
inputs only, must not try to reach Claude Design or ask for credentials, and
that any owner-exported bundle it consumes must be placed or kept under
`<mission dir>/design/` so the runner can attest it. The mission directory is
the first `allowed_paths` entry with its trailing `/**` removed (for FlightDeck,
`missions/flightdeck-<job_id>`).

### Capability evidence in the DeliveryManifest

The contract's `capabilities.design` declares a need; it is never proof of
execution. When the contract declares `capabilities` the runner records what
actually happened in `delivery.capabilities.design` and writes the same object
to `~/rbx/manifests/<mission>/capabilities.json`:

```json
"capabilities": {
  "design": {
    "required": true,
    "observed": false,
    "mode": "direct|degraded|unavailable",
    "degraded_mode": "repository_design_bundle|null",
    "bundle_ref": "missions/<dir>/design/<file>",
    "bundle_hash": "<sha256 of the bundle at the mission head commit>",
    "evidence_ref": "capability:<probe_fingerprint or sha256 of capability.json>"
  }
}
```

- `observed` is the probe result for this mission (`claude_design.headless_available`).
- `mode` is resolved from the final staged state, after verification:
  `direct` when the probe observed a headless interface **and** a valid
  attestation file was staged (see next section); otherwise `degraded` when the
  contract permits `repository_design_bundle`; otherwise `unavailable`, which is
  only reachable for `required: false`. A required capability that was observed
  but never attested, with no permitted degradation, stops the mission with
  `FailureManifest.phase = "capability"` before any branch exists.
- `required` echoes the contract value (absent counts as `false`).
- `degraded_mode` is present only in `degraded` mode and equals the contract's
  permitted mode. `direct` and `unavailable` carry no `degraded_mode` key.
- `bundle_ref`/`bundle_hash` are present iff mode is `degraded` and a
  repository design bundle was consumed: the first changed file under
  `<mission dir>/design/` other than `attestation.json` (repo-relative, no
  leading `/`, no `..`) that is staged as a regular blob (mode `100644`),
  hashed from the staged blob (`git show :<bundle_ref>`), never from the
  working copy. Symlinks and other non-regular entries are skipped and logged. `direct` never carries bundle fields, even if a
  bundle was also staged; Maestro fails closed on direct plus bundle.
- `unavailable` always has `observed: false`. An optional capability that was
  observed but left no attestation and has no permitted `degraded_mode` has no
  honest mode in the schema, so the runner fails closed with
  `phase: "capability"` instead of misreporting observation.
- `evidence_ref` points at the probe: the adapter's `probe_fingerprint` when a
  verified handshake was observed, otherwise the SHA-256 of `capability.json`.
- Contracts without `capabilities` keep the legacy delivery shape, byte for
  byte: no `capabilities` and no `design` key.

### The `design` attestation

`delivery.design` is emitted **only** when both hold:

1. the probe observed a headless interface (`observed: true`), and
2. the executor staged `<mission dir>/design/attestation.json` as a regular
   blob (mode `100644`, checked with `git ls-files -s`; a symlink entry is
   rejected) whose staged content (`git show :<path>`, never the working tree)
   is `{"interface":"cli|design_sync","probe_fingerprint":"<fingerprint>"}`
   with values equal to the probe's `interface` and `probe_fingerprint`.

Then `delivery.design = {"interface": "<cli|design_sync>", "status": "used",
"evidence_ref": "capability:<probe_fingerprint>"}` and
`capabilities.design.mode = "direct"`. The runner also writes it to
`~/rbx/manifests/<mission>/design.json`. When the interface was observed the
prompt names the fingerprint and the attestation path, and tells the executor
to write the file only if Design actually participated. A missing or mismatched
attestation is logged and treated as no evidence: the mode falls back to
`degraded` or `unavailable` and no `design` object is emitted.

Documented gap: Maestro validates `status: "used"` per the runner-result
schema, but Public Presence contract v1 still rejects it until it has a
capability registry. Degraded deliveries with a bundle map to Public Presence
`DesignProvenance.status = "bundle"`; degraded without bundle or unavailable map
to `unavailable`.

### Canonical schemas

- Capability report (this repository, owner):
  `bootstrap/ansible/roles/agent-workbench/schemas/capability-report.v1.schema.json`
  describes the adapter `capabilities` output and therefore the document behind
  every `evidence_ref`.
- Runner result envelope (rbx-maestro, owner):
  `docs/schemas/runner-result.v1.schema.json` (ExecutionManifest,
  DeliveryManifest including `capabilities` and `design`, FailureManifest,
  RunnerResultRequest). The mission contract, including `capabilities`, is
  `docs/schemas/agent-loop-mission-contract.schema.json` in the same repository.

## What this unblocks, and what it does not

Operational today:

- deciding whether a `claude_code_design` mission can be admitted at all, from
  observed host state rather than from the adapter name;
- a per-mission capability gate that fails closed when Design is required and
  no degradation is permitted;
- honest provenance in the delivery manifest: `capabilities.design.mode`
  resolved from staged evidence, and a `design` attestation only when the
  interface was observed and the executor attested it.

Pinned by `tests/runner-e2e-contract.sh` (missions 3 to 9: degraded bundle,
strict gate failure, observed-but-unattested degraded, observed-and-attested
direct, observed-but-unattested strict failure, optional unavailable, symlinked
attestation rejected) and by the static invariants in
`tests/runner-delivery-contract.sh`. This slice was implemented without a Codex
review round; the contract tests are the gate.

Still blocked on external capability:

- any live Claude Design call from a headless executor. This needs either a
  service-credentialed Claude Design API, which does not exist, or an explicit
  owner decision to place owner credentials on the workbench, which the current
  mission contract forbids.
