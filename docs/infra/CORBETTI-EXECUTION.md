# Corbetti provider-neutral repository execution

Corbetti is the initial RBX Agent Execution Workbench. It implements one
repository mission lifecycle independently of the coding provider selected for
a Mission.

## Contract boundary

Maestro supplies an immutable MissionSpec and owns admission, activation, lease
and terminal mission state. Corbetti owns:

- HTTPS pull, claim, heartbeat and terminal result transport;
- exact source-commit checkout into an isolated Git worktree for executable
  ingress contracts;
- executor discovery, invocation and normalized usage evidence;
- allowed and forbidden path policy plus measurable diff bounds;
- the admitted deterministic verify command;
- mission branch, commit and pull request delivery;
- local copies of the runner manifests for operational recovery.

The public communication domain remains outside Corbetti. Public Presence owns
creative-job state, owner-side asset import, semantic QA, publication policy,
channel credentials, receipts and reconciliation.

## Executor adapter

`rbx-executor-adapter.sh` is the provider boundary. The MissionSpec `executor`
value is authoritative for current executable ingress contracts. The adapter
rejects unknown values and does not choose a heuristic fallback. A legacy
compatibility contract that omits `executor` defaults to `claude-haiku`; the
resulting ExecutionManifest records that actual choice. New contracts must not
rely on this default.

The current compatibility set is:

| Executor | Provider adapter | Normalized structured result |
| --- | --- | --- |
| `codex` | `codex-cli-v1` | Codex JSON event stream |
| `claude-haiku` | `claude-cli-v1` | Claude stream JSON |
| `claude-sonnet` | `claude-cli-v1` | Claude stream JSON |
| `glm` | `glm-cli-v1` | Claude-compatible stream JSON |
| `kimi` | `kimi-cli-v1` | exit status; token usage may be unavailable |

This table is an implementation registry, not the architecture. Adding another
executor requires an adapter description, probe behavior, normalized run result
and contract tests. It does not change lease, policy, Git or manifest semantics.

## Capability evidence

Binary presence is discovery, not proof that a Mission will succeed. The
capability probe reports observed CLI version and structured-output support.
Credentials, network, quota and effective model availability remain
`unverified` unless a separate safe probe proves them. Reports contain no raw
credential or provider-response material.

Creative surfaces are separate capabilities. For example, a Claude Code
executor does not imply that Claude Design participated. Direct headless design
availability requires a versioned machine-readable handshake that proves a
service-credentialed interface without an owner login. Otherwise the reported
mode is the explicit `repository_design_bundle` degradation.

The same rule applies to any future design surface, browser controller or media
generator: the compatibility profile name is never provenance.

## Settlement

Corbetti submits one `ExecutionManifest` and exactly one terminal
`DeliveryManifest` or `FailureManifest` to Maestro's fenced, idempotent result
endpoint.

The `ExecutionManifest` records the resolved base commit, executor, provider,
model, adapter version, attempt and observed usage. The `DeliveryManifest`
records policy, verify, branch, head commit and pull request evidence. The
`FailureManifest` records the phase, structured stop reason and safe partial
evidence.

These runner manifests prove repository settlement only. They are distinct from
a Public Presence `CreativeDeliveryManifest` and from a channel publication
receipt.

## Execution freedom and controls

The executor chooses tactics inside the worktree. Controls are concentrated at
the immutable boundary and settlement points:

- pinned source commit for executable ingress contracts;
- fenced and bounded lease attempts;
- allowed and forbidden paths;
- diff size and file-count limits;
- execution timeout and token budget;
- deterministic verification;
- no direct push to the protected branch and no merge.

Provider child processes do not inherit the Maestro runner credential, GitHub
publication credential or kubeconfig. A worktree is not an operating-system
sandbox, so stronger host isolation remains separate hardening work.

## Compatibility

`rbx-agent-runner-v2.sh` uses the atomic Maestro result contract. Legacy result
calls are attempted only when an older Maestro explicitly returns HTTP 404 or
405. Validation, fencing, network and server errors never trigger a downgrade.

Legacy MissionSpecs may omit `source_commit`, in which case the executor checks
out the fetched `base_branch` tip and records it as `base_commit`. This is an
explicit compatibility path, not a pinning guarantee. Current Flight Deck V2
ingress requires `source_commit` and Maestro rejects a mismatched result.

The authoritative executable contracts are pinned by:

- `runner-provider-neutral-contract.sh`;
- `runner-capability-probe-contract.sh`;
- `runner-delivery-contract.sh`;
- `runner-e2e-contract.sh`.
