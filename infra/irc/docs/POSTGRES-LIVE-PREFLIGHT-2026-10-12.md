# IRC PostgreSQL live destination review

Assessment: **2026-10-12, America/Sao_Paulo**, approximately 22:27 to 22:32 BRT.
Repository baseline: `69b06fcb7a1ab18c2cd0a2bc60e717bacac323bf`.

## Summary

The declared database host, Jaguar, is reachable for read-only administration
through the existing ThinkCentre SSH identity. PostgreSQL 16.15 is running, but
no dedicated IRC destination was identified. A private application route,
verified TLS identity, application grants and recovery evidence remain open.

**Verdict: blocked for live mailbox migration and human-query activation.**
This is an assessment of the candidate host, not approval to provision or use it.
The adapter implementation merged in Infra #363 does not close these gates.

## Scope reviewed

- Existing workstation, ThinkCentre and Jaguar access, PostgreSQL catalogs,
  certificate metadata, HBA rule scope, firewall, host backup surfaces and clocks.
- Clock metadata on Corbetti, the existing IRC host.
- Secret reference names in the workstation and ThinkCentre `pass` stores.
  No specifically named IRC database reference was identified. No password was
  decrypted; existing SSH keys and local PostgreSQL peer authentication were used.
- No application data, chat payloads, Kubernetes Secrets or private keys were
  printed. No cluster inspection, service modification or mailbox migration ran.

## Findings

The [sanitized evidence](../../../docs/network/local/evidence/irc-postgres-preflight-2026-10-12.json)
contains measurements and hashes of private source records. Public evidence omits
host addresses, provider hostnames, configuration contents and credential values.

| # | Severity | Area | Finding | Required next action |
| --- | --- | --- | --- | --- |
| 1 | Blocker | Destination | Queries found no databases, roles or HBA entries with IRC names. No other dedicated IRC target was identified. | Select an explicit database/schema and separate owner/runtime roles per logical adapter; prepare declarative provisioning and `pass` references. |
| 2 | Blocker | Route | The workstation TCP connection to the declared public database endpoint timed out after four seconds. UFW is active and allows PostgreSQL from four individual inventory addresses. Jaguar has no Tailscale client; the workstation reports `NeedsLogin`. | Prepare and verify the selected private route, with scoped firewall/HBA rules, without broadening public access. |
| 3 | Blocker | TLS | PostgreSQL has TLS enabled, minimum TLS 1.2. The certificate names the provider host and has equal issuer/subject names. No application DNS identity or CA trust contract was verified; no `hostssl` rule exists. | Establish the intended hostname, trust reference and TLS-required application path; test verified identity from the adapter host. |
| 4 | Blocker | Recovery | The pgBackRest executable and repository directory were not found, no matching units were returned, and WAL archiving is off. No IRC-specific backup or restore evidence was verified in this assessment. | Establish backup coverage, encryption/key custody, an independent failure domain and a restore rehearsal before cutover. |
| 5 | Open | Permissions and capacity | No IRC runtime role exists under the inspected naming pattern, so its effective grants cannot be checked. The server allowed 200 connections and had 77 observed, one active. | Allocate a bounded connection allowance and validate actual runtime rights and denied cross-application access after approved provisioning. |
| 6 | Observed | Clocks | All four inspected hosts report NTP synchronization. Their reported offsets were between -31.862 and +14.154 ms. | Recheck at acceptance and after route or host changes; point-in-time reports do not prove future clock health. |

### Database and network interpretation

The running server reports PostgreSQL `16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)`.
The `pg_available_extensions` query returned no `pg_search` entry. This does not
match an assumption that ParadeDB search capabilities are already available on
this instance. The mailbox needs PostgreSQL transactions and does not require
installing a search extension to proceed.

`listen_addresses=*` does not prove public reachability. The inspected UFW rules
are source-scoped, and the workstation probe did not connect or send credentials.
Its timeout is consistent with a restricted path, but cannot identify the exact
network element that dropped the connection. No firewall rule was changed.

The two HBA `host` rules for all databases and users are restricted to IPv4 and
IPv6 loopback and use SCRAM-SHA-256. They are not broad Internet access rules.
The total of 92 parsed rules had no parse errors; none was `hostssl` or named an
IRC database/role. TLS being available does not prove it is required on a future
application route.

The certificate dates cover the observation time. That is only metadata from
the server's public certificate through authenticated SSH. No certificate chain
or application hostname verification succeeded in this assessment because the
workstation did not reach the PostgreSQL TLS handshake. The certificate and
trust store were not replaced or modified.

### Recovery interpretation

The server reports `archive_mode=off`, `archive_timeout=0`, no configured archive
command/library, zero archived WAL files and no last successful archive time.
The root and PostgreSQL crontab commands returned status 1 without a retained
reason; they did not establish a backup schedule or prove that crontabs were
absent. The matching host timer was `dpkg-db-backup`, which is not a PostgreSQL
backup. Approximately 330 GiB
was free on the inspected PostgreSQL filesystem; free space is not recovery
evidence.

These observations confirm that the repository's pgBackRest plan is not active
on this host. They do not prove the absence of every application backup. The
separate [Comms preservation record](../../../docs/runbooks/COMMS-PRESERVATION.md)
describes its own encrypted archive and isolated restore; it does not cover a
new IRC database. No backup, restore or outage test ran against production.

### Clock observations

| Host role | NTP synchronized | Reported offset |
| --- | --- | --- |
| Workstation / current local adapter | Yes | -31.862 ms |
| ThinkCentre / administrative path | Yes | +14.154 ms |
| Jaguar / candidate database | Yes | -1.821 ms |
| Corbetti / IRC server | Yes | -10.913 ms |

Offsets are separate NTP client reports, not a simultaneous pairwise clock test.
Different display time zones do not imply different UTC instants. Evidence uses
UTC timestamps; this review uses the operator's date and time zone.

## Risk assessment

- **Operational risk:** a live cutover now would add an unverified dependency
  without an established private application route or IRC recovery evidence.
- **Rollback path:** no deployed state changed, so no runtime rollback is
  required. Preserve the existing SQLite state and service configuration.
- **Blast radius:** future TLS, HBA or WAL changes on this shared host may affect
  other applications. Keep application-specific provisioning separate from
  changes requiring a database restart or a host-wide backup rollout.
- **Runtime safety:** retain fail-closed storage behavior, one authoritative
  writer and no automatic fallback or replay of uncertain work.
- **Derived state:** IRC logs remain conversation history; they do not replace
  the adapter's claims, deduplication and delivery-state records.

## Governance impact

The database placement and backup direction reference the existing database
architecture and ADR-0012. Any different backup or network design needs an
explicit decision; this review does not silently replace those contracts.
Provisioning will affect production roles, secrets and access controls and
therefore needs a reviewed, target-specific implementation and authorization.
Application licensing and IRC channel membership grant no database privileges.

## Open decisions

- Database custody, final names and the runtime connection allowance are not
  assigned by this review.
- The private endpoint, CA delivery mechanism and any required Headscale
  enrollment need a concrete reviewed configuration.
- The backup destination, key-recovery owner and RPO/RTO targets remain
  unselected. No third-party account or service was purchased or provisioned.
- Any database restart, backup rollout or dedicated-role creation requires its
  own reviewed operational plan; none is included in this assessment.

## Concrete preparation sequence

1. **Reconcile the private route.** Compare deployed Headscale policy with its
   repository implementation before enrollment, including the existing roadmap
   warning about replacing policy. Select the database hostname and verified
   TLS trust path. A temporary administrative SSH path is not an installed
   runtime service or an approved application transport.
2. **Prepare custody and recovery.** Confirm whether the existing pgBackRest
   direction is retained, choose the independent encrypted backup destination,
   identify key custody, and define RPO/RTO targets. Prepare installation,
   restart impact, preservation and reversal as a reviewable change. Exercise
   restoration in isolation before admitting real IRC state.
3. **Prepare dedicated provisioning.** Define database/schema, owner/runtime
   roles, connection allowance, source restrictions, default privileges and
   `pass` delivery references. Preserve `human_queries: false`. Do not reuse the
   Comms database or another application's password.
4. **After the target is approved and ready**, verify permissions, private TLS,
   clocks and recovery. Then follow the [migration runbook](POSTGRES-MAILBOX-RUNBOOK.md)
   with quiescence, a consistent source snapshot, import hold and reconciliation.
5. **After migration acceptance**, run the bounded human-query test and only
   then integrate the subscription executor. A merged change is not evidence
   that either step ran.

## State-policy record

| Field | Result |
| --- | --- |
| State changed | Local private diagnostic files and repository documentation only; normal access logs/statistics may record reads |
| Source of truth | Existing adapter store remains authoritative; Git for implementation/IaC; approved secret store for credentials |
| Remote backend | Jaguar inspected as candidate, with PostgreSQL 16.15; dedicated IRC target remains unprovisioned/unverified |
| Backup and last execution | pgBackRest/WAL archive absent; no IRC backup execution or independent receipt verified |
| Last tested restore | No selected IRC destination restore established |
| RPO/RTO | Neither targets nor measured recovery values established |
| IaC | Existing database/backup declarations inspected; no provisioning or apply executed |
| Drift / unfulfilled assumptions | Declared pgBackRest direction not active; `pg_search` not available on inspected instance; no claim that either was previously deployed |
| Residual local state | Current SQLite mailbox and Halloy experiment preserved; mailbox contents not read |
| Blocking gaps | Dedicated target, runtime credential delivery/grants, private TLS path, backup/restore and cutover rehearsal |

## Evidence and validation

Read-only operations: PostgreSQL catalog/settings queries, selected
`timedatectl` properties, public certificate metadata, UFW status, selected
systemd/crontab metadata and one TCP probe. SSH used strict existing host-key
verification at both hops. `pass` inspection returned entry names only.

No database/role creation, HBA edits, certificate replacement, package install,
secret write, backup execution, service restart or IRC message was performed.
The next safe action is a reviewed preparation of the private route and recovery
plan, followed by the dedicated database declaration. Production changes are
separate from this read-only assessment.

Documentation register: R4 with R6; RBX Voice System v0.1. Technical identifiers,
commands and measured uncertainty are retained.
