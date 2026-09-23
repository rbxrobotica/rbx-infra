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

A delivery that used such a bundle records it as
`design.status = "bundle"` with the bundle's path and hash. A delivery produced
without any design bundle records `design.status = "unavailable"` and the probe
reason. **Neither is allowed to record `design.status = "used"`**, which is
reserved for a live Design interface that does not exist today.

## Probing, instead of assuming

```bash
rbx-executor-adapter.sh probe claude-sonnet   # one executor
rbx-executor-adapter.sh capabilities          # every executor + claude_design
```

`probe` reports only what it observed on the host: whether the CLI is on PATH,
the version the binary actually printed, and whether the CLI advertises the
structured-output flags the Corbetti result parser depends on. A CLI that is
present but cannot emit structured output is reported **unavailable**, because
presence alone does not make a mission succeed.

It deliberately does **not** probe credentials, network, quota or model
availability. Verifying those means reading credential material or spending a
billable call, so they are listed under `unverified` and stay the caller's
problem. A capability report must never contain credential material; the
contract test asserts this with decoy environment variables.

## What this unblocks, and what it does not

Operational today:

- deciding whether a `claude_code_design` mission can be admitted at all, from
  observed host state rather than from the adapter name;
- honest provenance in the delivery manifest about whether Design participated.

Still blocked on external capability:

- any live Claude Design call from a headless executor. This needs either a
  service-credentialed Claude Design API, which does not exist, or an explicit
  owner decision to place owner credentials on the workbench, which the current
  mission contract forbids.
