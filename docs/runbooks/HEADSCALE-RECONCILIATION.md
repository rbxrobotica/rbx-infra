# Headscale reconciliation before private IRC access

Status: preparation only, October 12, 2026. No deployment, restart, enrollment,
new grant or database migration is part of this increment.

## Observed deployment and conflicting sources

The [evidence record](../network/local/evidence/headscale-reconciliation-2026-10-12.json)
records the 23:06 BRT inspection. Headscale 0.29.3 was active on Eagle, with two
online nodes. The configuration references `/etc/headscale/policy.hujson` in
file mode. The policy contains two grants: administrative SSH to Corbetti and
a POCO TCP/18080 probe to Corbetti. These are observations, not proposed grants
for a new device. Neither is a phone IRC permission.

Embedded DERP is disabled, the public Tailscale DERP map is configured, and
MagicDNS and local resolver override are disabled. This differs from the
original Git template and ADR-0011. Publishing the public relay map does not
prove a particular connection used a relay; no connection-path test ran here.

Three sources must not be conflated:

| Source | What it establishes | Limitation |
| --- | --- | --- |
| Main at `be7686e2` | Legacy role/template and original DERP decision | Omits policy, replaces configuration before validation, permits client downgrade |
| ThinkCentre branch `feat/headscale-governed-rollout`, `4f84a1a0` | Nine later commits, separate identities, file policy, later client pin and local backup helpers | Not published; initial role writes an empty grant list over the existing policy; validation can open the live database |
| Eagle metadata and live policy check | Current version, file policy, DERP profile, service and backup metadata | Point-in-time observation; no new traffic matrix or recovery drill |

The recovered commits were fetched into a separate local ref without rebasing,
checking out or changing the ThinkCentre worktree. Its pending POCO playbook
edit remains untouched; the evidence records its checksum. Do not cherry-pick
all nine commits blindly, overwrite that worktree, or present their historical
acceptance reports as current tests. The older D4 report is not proof that
`rbx-local` is enrolled today: its October 12 preflight reports `NeedsLogin`.

The recovered role is also unsafe to reapply unchanged: it installs
`grants: []` unconditionally. Preserving current policy means retaining its
bytes, not merely ensuring that a policy file exists. Reconcile the later ADR
with the current ADR catalog before assigning an identifier or claiming that
the original relay sovereignty requirement remains satisfied.

## Immediate protection

Both legacy roles, `headscale` and `tailscale-node`, now stop at an unconditional
first task, including check mode. This deliberately quarantines the historical
rollout until a reviewed replacement exists. There is no override variable.
Do not skip the `always` tag or jump past it with `--start-at-task`. The legacy
client task also no longer allows an implicit package downgrade.

A merge only updates source code. It does not disconnect current peers, change
the running policy, uninstall anything, restart services or enroll phones.
Removing the guards requires a reviewed implementation and the evidence below;
reverting the guard is not an operational rollback procedure.

## Repeatable file observation

`scripts/network/headscale_preflight.py` reads only the explicitly selected
configuration and policy files. It emits sanitized structural observations and
hashes, without configuration values, usernames, addresses, key material or
raw exception text. It does not invoke Headscale, connect to its API, open its
database or perform a deployment. Python 3 and PyYAML are prerequisites; do not
install dependencies on a server merely to perform this audit.

For local synthetic files or an already authorized host session:

```sh
python3 scripts/network/headscale_preflight.py \
  --config /etc/headscale/config.yaml \
  --policy /etc/headscale/policy.hujson
```

An optional `--candidate-config` accepts a separately rendered YAML candidate
and detects selected policy, relay and DNS changes. Use fixtures for candidates;
do not stage a candidate over the effective server file. A successful observation
is never deployment approval or proof of traffic permissions. Strict JSON
policies are inspected structurally; unsupported HuJSON is reported as unknown,
not silently accepted. Empty or missing rule containers require explicit review.

Keep raw operational files on their existing host. When invoking the script
through SSH stdin, use the existing trusted host keys and an authorized account;
do not disable host-key verification, copy private SSH keys or forward an agent.
Store audit JSON in a private operator directory. Only reviewed sanitized
extracts belong in this public repository.

The file auditor returned `observation_only` on Eagle; its file hashes match
the earlier metadata receipt. This confirms structural observations only.

The current policy also passed the running server's `policy check --file`
RPC, without database bypass. That confirms acceptance against current users and
nodes, not an allow/deny packet test. Do not use
`--bypass-grpc-and-access-database-directly` against a running server.

One native `headscale configtest` was called during the initial inspection and
returned zero. It is **not a pure parser**: v0.29.3 initializes server state,
can create keys and can run database migrations. Do not infer absence of database
side effects from its exit code or repeat it as a read-only audit. Candidate
validation must use disposable, isolated state and no live database/key mounts.
Sources: [configuration documentation](https://headscale.net/stable/ref/configuration/),
[versioned configtest](https://github.com/juanfont/headscale/blob/v0.29.3/cmd/headscale/cli/configtest.go),
[server initialization](https://github.com/juanfont/headscale/blob/v0.29.3/hscontrol/app.go),
and [policy RPC implementation](https://github.com/juanfont/headscale/blob/v0.29.3/hscontrol/grpcv1.go).

## Recovery evidence and limits

Eagle has an enabled daily backup timer (`03:17 UTC`, with delayed execution). Its last reported successful execution
and newest archive were at 2026-10-12 03:30:45 UTC, or 00:30:45 BRT; 15 local
archives were listed. Archive contents were not read and no new restore ran.
A successful systemd result and a file's timestamp do not establish a restorable
external backup. Older branch reports describe an encrypted off-host copy and
isolated restore, but their present custody, availability and freshness were not
verified in this inspection. Do not claim that all external backups are absent.

| Affected state | Source of truth and remote checkpoint | Recovery status |
| --- | --- | --- |
| Proposed audit and guards | This repository's feature branch and PR | Reconstructible source; no deployment |
| Live Headscale policy, config, SQLite, Noise/DERP keys and ACME cache | Existing Eagle state; exact recovery custody still to verify | Local archive metadata only; current external recovery unverified |
| Nine historical rollout commits and pending POCO edit | ThinkCentre source; committed objects also fetched locally | Original retained; publication/import pending review; pending edit still local |
| Future IRC PostgreSQL mailbox | Dedicated Jaguar target proposed in the merged database contract | No cutover; database/role, private route, verified TLS and recovery still pending |
| Credentials and recovery keys | Existing `pass` custody, values not read here | Remote encrypted store and offline recovery-key evidence still to verify |

RPO/RTO and accountable recovery owner remain to be recorded. PostgreSQL's
ADR-0012 names rsync.net Zurich. The operator confirmed on October 12 that this
account does not exist yet and requested requirements and costs. See the
[rsync.net readiness and budget](RSYNC-NET-READINESS.md). No purchase or account
creation is included. Extending this destination to Headscale remains a proposal.

## Next implementation and operator gate

The [candidate preparation increment](HEADSCALE-CANDIDATE.md) now reproduces
the observed baseline from recovered Git source, preserves original bytes and
tests the complete synthetic candidate in isolated native Headscale. It provides
local preparation only; the deployment role and live rollback remain unimplemented.
The [mobile IRC design](PRIVATE-IRC-MOBILE.md) also records the newly observed
loopback bindings and self-signed TLS trust gap. These advance items 1, 2 and 4
below without claiming that their live acceptance or recovery gate is complete.

1. Review and publish the necessary parts of the recovered source without
   importing stale inventory, general hardening changes or historical acceptance
   claims as current state. Preserve the pending POCO edit separately.
2. Build a replacement control-plane role that requires an exact known baseline,
   preserves the policy byte for byte, validates the full candidate with isolated
   synthetic identities/state, and refuses unexplained drift before any write.
   Test invalid candidates, existing grants, no-op behavior, concurrent changes
   and rollback. Never downgrade clients as a side effect of reconciliation.
3. Confirm encrypted external custody and perform an isolated restore with
   checksum, SQLite integrity and operational recovery evidence. Record owner,
   retention, monitoring and RPO/RTO. Do not restore over the live database.
4. Prepare a service-specific private IRC endpoint: inspect Ergo's actual bind
   and certificate identity, keep plaintext loopback-only, verify TLS name/CA,
   and propose a phone-only grant to the IRC TLS service. Keep administrative
   SSH and database access denied to phone identities. No LAN advertisement,
   resolver replacement or exit node is needed.
5. Present the exact configuration/policy diff, traffic matrix and rollback for
   live authorization. A phone test follows account and endpoint readiness; USB
   access to the POCO or A02 is not required for this preparation.

The recovery skill requires: "If any required field is absent, unknown, stale,
or exclusively local, stop before mutation and present a remediation plan."
This blocks live promotion while custody and restore evidence remain open;
it does not block reproducible source and synthetic-test preparation.
Canonical policy: `rbx-agent-layer/skills/rbx-state-backup-iac-recovery/SKILL.md`.
