# Automatic DNSSEC renewal for the undelegated RBX pilot

Authorized November 13, 2026: implement and validate automatic signature renewal,
publication and alerts before considering a paid ClouDNS commitment. Only
`cloudns-xfr-pilot.rbxsystems.ch.` is in scope. Production zones, parent DS/NS,
the production signer, billing and account factors are outside this change.

## State and recovery preflight

| Field | Declared boundary |
| --- | --- |
| State | Existing disposable lab signer; a dedicated constrained SSH publisher credential; a separate renewal heartbeat; public publications and two isolated systemd services |
| Source of truth | This directory for code/Ansible/units; resilience PR11 for the original independent public DS, fixed signed-content baseline and execution evidence |
| Remote | Private GitHub source/evidence branches; existing encrypted private password-store remote; Healthchecks outside Contabo |
| Backup | Existing lab signer already restored from a fresh encrypted remote fetch. New SSH and signal credentials must be encrypted directly in pass, pushed and read back before deployment; never deploy a unique local private key |
| Runtime protection | Only service-specific lab signer, constrained publisher and heartbeat through host-wrapped systemd credentials. No owner GPG/pass, management API key or administrative SSH key in runtime. Protected runtime credential files exist; no plaintext private material is written to persistent storage |
| Public state | Atomic public candidate/current files on the workstation and independent origin; public baseline and code in private Git. Pending candidates survive publication interruption and retry byte-for-byte; never decrement a serial to roll back |
| Restore | Earlier same-workstation GPG canary and lab signer restoration remain valid narrow observations. Validate origin restart and pending replay in this cycle. Founder-independent root recovery remains unfinished |
| RPO/RTO | Private identity: latest encrypted custody commit. Public content is fixed and reproducible; newest publication retained at producer and origin. Renewal every hour at three days remaining; missing job target two hours plus delivery, DNS-health target about 30 minutes after last success. Technical publication recovery target 15 minutes, to be measured |
| Owner | Leandro; primary executor handles credentials, recovery, mutations and publication |
| Local risk | Host-wrapped credentials and release are reconstructible from reviewed source plus encrypted remote custody. The existing owner GPG offline emergency kit/successors are not qualified; this remains a disposable synthetic pilot |
| Shared host | Jaguar had free public IPv4 port 53, only loopback resolvers, active PostgreSQL/k3s and no new origin release/state. New origin is one dedicated unprivileged account, 128 MB / 10% CPU, exact provider-IP DNS access and an owned firewall chain. No package installation or production process change |
| Cost | Existing workstation/Jaguar, free Healthchecks allowance and current ClouDNS trial; no order or charge |

## Flow and security boundaries

The original DS/DNSKEY and complete semantic content/TTLs/NSEC chain are pinned
independently. Only SOA serial and generated signatures may change. The signer
checks its key against the public anchor, signs all 24 authoritative RRsets for
seven days, validates before selection and renews with three days remaining.
RFC1982 rejects equal/older/ambiguous publication serials; wraparound is tested.
One writer holds a lock. NTP synchronization and backwards-time checks precede
signing. A pending public candidate survives timeout/crash and is reused rather
than re-signed on retry.

The signer has a separate DynamicUser and protected credential directory. Its
SSH key can invoke only `inspect` or `publish` on an unprivileged forced-command
gateway. That account has no sudo or administrative command access. It accepts
only a bounded public zone with the expected full content, original key,
currently authentic signatures, sufficient lifetime and advancing serial. It
atomically selects the candidate, signals its isolated NSD and verifies actual
authoritative replies. The producer then requires all four managed authorities
over UDP/TCP to converge before recording success. Failed verification never
silently reverts a serial. Prior candidates remain recoverable.

The independent monitor retains the original immutable key/content baseline,
allows authenticated SOA serial advance, checks a persistent observed serial
floor and rejects stale/mixed responses. It does not trust a signer-written
replacement anchor. This fixes the old immutable-SOA comparison without merely
accepting arbitrary newer data. Its observed serial floor is a reconstructible
cache; losing it reduces rollback detection to the baseline/validity boundary,
which remains an explicit limitation.

Renewal/publication has its own hourly heartbeat with one-hour grace. A verified
no-op still checks selected origin and managed DNS health. The existing
15-minute DNS health heartbeat remains separate. No success is sent merely
because signing, SSH or SIGHUP returned successfully.

## Deployment and verification

Build a public-only stage with `stage.py`; its manifest pins source, baseline,
known SSH host key, pure-Python DNS dependency and the previously verified
official NSD binary. `prepare-custody.py --work-dir <private-cache>` prepares
only the dedicated SSH/signal entries, preserves the other checks and verifies
the SSH identity from a fresh encrypted remote fetch. Copy its public key into
the public stage as `publisher-public.key`; no private material belongs there.
Ansible declares the remote origin/account/forced key and
local protected service/monitor override. Review check/diff before apply; keep
the remote administrative SSH agent only for this bounded provisioning/drill,
then stop it. It is never part of unattended operation.

`activate-local.py` authenticates through the operator terminal and passes three
service-specific credentials only through stdin to `activate-root.py`. The
root helper refuses unexplained drift or pre-existing renewal state, verifies
the runtime key against the original DS, wraps credentials, runs the declared
plan/apply/idempotence checks and starts the exact renewal/monitor units.
Owner passwords never enter arguments, chat, files or capture.

Validate actual new signatures/serial on the origin and managed authorities,
unchanged original anchor/content, subsequent verified no-op, failure and
recovery notification delivery, and service/timer state. Offline tests cover
signing/policy errors, publication interruption, failed convergence, rollback,
wrong content/key, clock/expiry and atomic selection. Publish sanitized receipts
separately from older failures; do not promote a production H-dns grade.

## Observed implementation checkpoint

Forty-two offline renewal controls and seven existing monitor controls pass;
relevant CI is green. The new constrained SSH identity was recovered from a
fresh encrypted private-remote fetch. The separate renewal check is configured
at 3600/3600 seconds with its email channel and the other two checks preserved.

The first recursive check-mode copy exceeded 90 seconds without apply. It is
preserved as a failed preview. The uncompressed public-bundle replacement
passed preview, actual application (`ok=9`, `changed=8`, no failures) and a
subsequent preview with `changed=0`. Root-owned forced authorization, dedicated
account/state ownership, resource limits, exact 12-rule firewall chain and
active PostgreSQL/k3s were independently verified. Real arbitrary SSH command
and tampered publication requests were rejected; the selected zone/hash stayed
unchanged. Unauthorized external TCP/UDP DNS requests timed out while the
authorized origin control remained healthy.

Local protected renewal activation awaits operator-terminal authentication.
The active old monitor remains healthy; no new automatic signatures or renewal
email drill are claimed yet. The prepared finite `owner-alert-drill.py` will
exercise the real renewal orchestration against a stopped/restarted origin,
using an ephemeral constrained SSH-agent transport. It is explicitly an owner
drill, not evidence that the protected systemd unit failed by itself. It refuses
to publish or overlap an imminent scheduled run, restores the origin in its
cleanup and does not disable the timer. Never run the outage drill before local
activation and its first successful publication.

## Remaining boundaries

The workstation is still the online signer/producer failure domain. Its loss
must alarm; reconstructing host-wrapped credentials still needs owner recovery.
IP transfer ACLs are not TSIG. IPv6, independent monitor vantages, parent
discovery, production signing policy/key custody and unattended failover of the
signer remain unqualified. ClouDNS's confirmed stale-serving policy remains
unchanged. The trial notice showed November 19 expiry without a cutoff time;
automated signing does not buy or extend the provider subscription.
The vendored binary/dependency release is a reviewed pilot snapshot, not an OS
automatic-update enrollment; production adoption needs an explicit patch/rebuild
procedure.
