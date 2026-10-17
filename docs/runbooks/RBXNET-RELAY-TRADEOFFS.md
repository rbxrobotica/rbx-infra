# RBXNet relay tradeoffs and pilot budget

Status: analysis requested October 17, 2026; no relay selection or deployment.
For this review, the operator's “zug-coded” preference means few components,
modest CPU/RAM use, verifiable artifacts, explicit control and maintainable
recovery. It is a design criterion, not a benchmark or a formal standard.

## Decision to evaluate

Keep the currently working access while measuring one workstation and the POCO.
Do not purchase another VPS or change the DERP map before establishing the
baseline and reconciling the intended sovereignty requirement. The recommendation
is a staged comparison, not acceptance of the observed drift as the final design.

The historical ADR-0011 requires embedded DERP only. The October 12 observation
instead found embedded DERP disabled and the public map configured. Re-observe
before applying anything; neither a merged PR nor an old report proves runtime.
An accepted relay choice must amend or supersede the ADR explicitly, without
silently editing its historical evidence. Do not confuse the Headscale control
plane, WireGuard transport, DERP relay, IRC TLS, or the existing SSH tunnel.

DERP helps discovery and relays traffic when a direct path is unavailable. It
forwards WireGuard-encrypted packets, without access to their plaintext. Direct
peer traffic is not the same as “no external dependency”: clients may still use
relay/discovery services during establishment. See the official
[Tailscale DERP description](https://tailscale.com/docs/reference/derp-servers).

## Alternatives

| Option | Incremental RBX resources | Benefit | Cost or failure exposure | Pilot position |
| --- | --- | --- | --- | --- |
| Public DERP map | No new RBX relay host; client network/battery use still exists | Few components to maintain; geographically distributed fallback | External provider, observable transport metadata, unverified contractual guarantees; conflicts with embedded-only ADR | Baseline only if a fresh audit confirms this remains the deployed map |
| Embedded DERP on existing Eagle, own-only map | Existing host CPU/RAM, bandwidth and maintenance; new invoice only if quota/capacity requires it | RBX controls advertised relay and operation without another daemon/VPS | Control and relay share one failure domain; no public fallback; ports, TLS, capacity, abuse control and recovery must be operated | Smallest own-relay candidate if sovereignty is the primary constraint |
| Embedded plus public DERP | Embedded costs plus retained external dependency | More recovery paths than a single own relay | Cannot promise all relayed traffic stays on RBX; selection is not a guaranteed “own-first” policy | Evaluate only if the operator accepts external fallback explicitly |
| Separate own DERP host | Additional host, monitoring, updates, certificates, backup and operations | Can separate relay and control failure domains | More components and invoices; independent relay still has bootstrap/authentication dependencies | Defer unless measured availability, geography or capacity warrants it |

Headscale documents that disabling public relays while keeping only one embedded
server introduces a single relay failure point. It also documents STUN needs,
client verification and diagnostic commands. Confirm behavior against the pinned
release before implementing; do not copy current defaults blindly.
[Headscale DERP reference](https://headscale.net/stable/ref/derp/).

Own relay is about custody, metadata exposure and operational control; it is not
needed to make WireGuard payload encrypted. It also does not hide IRC messages
from Ergo/authorized server operators. Public relay access is not permission for
an outsider to join RBXNet or its IRC rooms. Conversely, a compromised enrolled
device retains whatever its network and application policies allow.

Do not introduce a peer-relay implementation merely because another product's
latest docs suggest it: compatibility with RBX's Headscale/client versions and
the mobile scenario has not been established here. No extra gateway, bouncer,
router replacement or Kubernetes workload is required for this comparison.

## Cost accounting without invented quotations

No new provider quotation or invoice was collected. The existing Eagle hosting
cost, transfer allowance, overage and spare resources remain inputs to verify.
“No additional host purchase” is not “zero operating cost”, and public service
availability is not a contractual SLA demonstrated by this review.

Use the following monthly estimate with actual billing and measurement:

`incremental cost = extra hosting + charged transfer + operations hours × internal hourly cost`

For a one-way relayed payload of B bytes, the relay receives roughly B and sends
roughly B, before protocol overhead. A provider may charge only egress or combine
directions; do not price 2B without checking that contract. IRC conversation
volume and ML/artifact transfers have different capacity profiles. Keep bulk
files off IRC and do not extrapolate an IRC pilot to fleet-wide throughput.

| Budget input | Measurement / owner action |
| --- | --- |
| Hosting | Check existing Eagle allowance and incremental upgrade cost; avoid double-counting its full shared invoice |
| Transfer | Measure relay ingress/egress and confirm provider metering and quotas |
| Operations | Track setup, updates, certificate maintenance, incident and restore time |
| Capacity | Record idle/active CPU, RSS, open connections and bandwidth; compare with actual headroom |
| User cost | Record phone battery impact, reconnect delay and failed sessions on Wi-Fi/mobile data |
| Reliability | Record direct/relayed path, relay location and recovery under representative failures |

The initial budget recommendation is **no additional VPS commitment**. A second
relay is justified only by a documented requirement and measured benefit that
exceeds its incremental cost. The existing SSH path is retained during the pilot,
but it shares Corbetti and Internet dependencies: it is not full disaster recovery.

## Bounded comparison and decision gate

1. Record current Headscale/client versions, effective relay map and configuration
   provenance; distinguish observed configuration from the path actually used.
2. Establish the phone's USB/read-only baseline and existing VPN/Private DNS state.
   Do not change the control plane merely to make a diagnostic pass.
3. Once the private IRC TLS endpoint and per-device account are ready, measure
   normal Wi-Fi and mobile-data connections. Use repeated sessions and record
   timestamps, connection success, time to usable authenticated channel, selected
   path, visible gaps, CPU/RAM and phone battery conditions. A single connection
   or background battery sample is not a performance benchmark.
4. Propose the exact isolated/limited embedded-relay experiment only after
   recovery prerequisites and rollback are established. Verify known-client
   admission and unknown-client refusal so the relay does not become an open
   bandwidth proxy. Test failure behavior without deliberately breaking the
   production service or removing the operator's recovery path.
5. Compare baseline and candidate using the same devices/network conditions.
   Agree quantitative latency/battery/recovery targets before scoring results;
   this document does not invent an availability promise or RPO/RTO.
6. Record whether external relay metadata/dependency is acceptable, accepted
   cost and maintenance ownership, evidence and limitations in the reconciled ADR.
   Only then change the production map or retire the automatic SSH tunnel.

Future testing with Flavia or her devices needs her participation, a separate
revocable guest identity and a deliberately limited test scope. Do not share an
operator account or assume company/channel/history access. This is future work,
not a reason to build a multi-tenant mobile gateway now.
