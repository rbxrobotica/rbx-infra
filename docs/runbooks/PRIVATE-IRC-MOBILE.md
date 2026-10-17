# Private IRC access for mobile devices

Status: proposed endpoint design, October 12, 2026. No DNS, certificate, port
binding, firewall, Headscale grant, account or phone enrollment changed.

October 17 operator review: separation of network/application/command permissions,
revocable per-device credentials and the server-visible nature of IRC messages
were accepted as design principles. The operator requested explicit DNS/TLS
documentation and a lightweight, sovereignty-aware relay cost/benefit review.
The POCO is available for USB diagnostics; this does not establish endpoint
readiness or authorize changing production DNS, policy or listeners. Future
external tests with Flavia or her devices are an option, not current enrollment
or access authorization. See [relay tradeoffs](RBXNET-RELAY-TRADEOFFS.md).

## What the existing access actually provides

At 23:25 BRT on October 12, Corbetti's Ergo container was running from the
configured `ghcr.io/ergochat/ergo:v2.19.1` image. Host TCP/6667 and TCP/6697 were
published only on IPv4 loopback. This supports the current tunnel-based desktop
access; it does not make either port reachable from a phone on the mesh.

A TLS probe to loopback TCP/6697 received a certificate with CN and DNS SAN
`irc.internal.rbx`, plus an IP SAN for `127.0.0.1`. Its validity is September 24,
2026 to September 24, 2027. Verification against the server's system trust store
failed with a self-signed-certificate error. The early trust failure is not proof
that hostname validation passed. No IRC authentication or message was sent.
Only public certificate metadata was inspected; no private key was read.

The current phone grant to Corbetti TCP/18080 is a probe permission. It grants
no IRC access. Headscale identities and IRC accounts are independent controls:
network reachability does not authorize a channel or access to its history.
See [evidence](../network/local/evidence/headscale-candidate-2026-10-12.json).

## Proposed endpoint and trust

Use one TLS service reachable through Corbetti's private mesh address, with an
explicit service port and per-device IRC credentials. Keep plaintext IRC bound
to loopback and retain the workstation's existing tunnel during the pilot.
Do not bind the service indiscriminately to all host interfaces or publish an
Internet-facing IRC port as part of this proposal.

`irc.rbxsystems.ch` is a candidate connection name, not a created DNS record or
approved final name. Select and document how phones resolve it to the private
mesh address while retaining `--accept-dns=false`. An explicit DNS record may
work without replacing client resolvers; test Android, mobile data, configured
Private DNS and DNS rebinding protections before relying on that choice.

Preferred trust approach for evaluation: a public certificate for the chosen
owned domain, issued with ACME DNS-01 while the IRC service stays private.
DNS-01 verifies domain control independently of public service reachability.
Use a narrowly scoped challenge credential or delegated challenge zone; define
the renewal owner, key custody, deployment/reload mechanism, expiry alert and
rollback before enabling automation. A domain certificate can disclose the name
through public certificate transparency logs; choose the name accordingly.
[Let's Encrypt challenge documentation](https://letsencrypt.org/docs/challenge-types/).

A private CA is an alternative if RBX chooses to manage CA trust, distribution
and revocation on every client. Test the actual Android client's trust behavior;
installing a user CA does not by itself prove that an app accepts it. Neither
option permits disabling hostname or certificate verification. The existing
self-signed certificate is not silently promoted into phone trust.

### DNS and certificate contract to complete before provisioning

DNS discovery, TLS server authentication, mesh membership, IRC authentication
and channel/history authorization are separate checks. A DNS name or certificate
does not enroll a device or grant a room. Modes such as secret/invite-only are
not a complete company/tenant authorization model.

| Decision | Proposed pilot contract | Evidence required before live use |
| --- | --- | --- |
| Client name | One owned FQDN, provisionally `irc.rbxsystems.ch` | Operator selects name; certificate SAN matches exactly |
| Destination | Corbetti's stable mesh address, TLS TCP/6697 | Fresh node/address inventory; private bind and firewall; no public IRC listener |
| DNS resolution | First evaluate a normal authoritative record resolving to the mesh address | Works with Wi-Fi, mobile carrier DNS and Android Private DNS; record discloses name/address and may encounter rebinding filters |
| Alternative resolution | Split DNS only if the actual client resolver path is explicitly designed | With `accept-dns=false`, Headscale-pushed DNS must not be assumed effective; no hidden global resolver replacement |
| IPv6 | Publish AAAA only when that exact path is validated | Positive and negative IPv6 tests; otherwise avoid advertising an untested path |
| Certificate issuance | Public ACME DNS-01 candidate, narrowly scoped/delegated challenge authority | Domain control, provider support and least-privilege credential custody confirmed |
| Renewal | Named owner, scheduled renewal, validated atomic install and documented Ergo reload | Staging/dry-run where supported, installed certificate readback, expiry alert tested; issuer success alone is insufficient |
| Failure | Reject wrong name, untrusted CA and expired certificates | No verification bypass; retain working desktop SSH route for recovery |
| Recovery | Preserve current key/certificate securely and restore only a valid matching pair | Restricted backup, rollback procedure, custody outside the dependent service and restoration evidence |

Avoid making certificate renewal depend solely on the same mesh path it is
supposed to recover. DNS-01 challenge credentials stay on the authorized issuer,
never in the phone, IRC, Git or an agent prompt. Public certificates expose the
chosen hostname through certificate transparency; a private IP DNS record is
not an access-control mechanism. A public CA removes private-root distribution
work, but not DNS/issuer dependency or renewal responsibility.

Use a proposed alert threshold of 30 days remaining, with escalation at 14 and
7 days, subject to the selected certificate lifetime and renewal schedule. These
are proposed thresholds, not deployed alerts or measured response guarantees.
Record certificate serial/fingerprint and validity as public metadata; never
attach private keys or credential-bearing configuration to evidence.

For an authorized test client, verify the peer using the selected FQDN as both
SNI and hostname and a trusted CA store. Test failure cases with synthetic or
isolated fixtures, not by replacing the live certificate with an invalid one.
Changing Android Private DNS, enabling a VPN, switching to mobile data or
altering battery exemptions are separate observable test steps; record original
settings and restore them after the agreed test.

### USB diagnostic boundary

USB inspection may establish the actual model, Android/API version, presence and
version of Tailscale/IRC clients, Private DNS mode and battery restrictions. The
USB vendor/product label is not a reliable device-model identification. Do not
collect photos, messages, accounts, full bug reports, raw application databases,
tokens, private DNS provider names or device identifiers for public evidence.

ADB needs both host USB permissions and the phone's debugging consent. Keep its
server on loopback and never enable wireless ADB as a workaround. Install only
the required tools from a verified source; do not install Android Studio for
this pilot. Record version and provenance. A locally computed SHA-256 identifies
the downloaded artifact; it is not an upstream signature or certificate.

On the inspected Mint host, `android-sdk-platform-tools-common` is the distribution
package supplying USB rules. The operator is already in `plugdev`; installing
the rules requires local sudo authentication. After installation, reconnect the
USB cable and approve this computer's debugging key on the phone if prompted.
Do not run ADB as root or make the USB node world-writable to bypass this step.
See the [October 17 readiness record](../network/local/evidence/poco-usb-readiness-2026-10-17.json)
for the initial permission failure and verified host-tool provenance; it contains
no claim that phone inspection or IRC acceptance has completed.

Read-only readiness does not require changing VPN or DNS settings. Actual IRC
acceptance remains conditional on the service and account readiness above.

## Proposed acceptance matrix

Prepare the exact change against the fresh observed policy. Preserve every
existing grant unless its removal is a separate authorized decision. The
matrix below describes the new phone IRC identity, not a retroactive claim
about the existing POCO probe identity.

| Source and destination | Expected result | Evidence required |
| --- | --- | --- |
| New phone IRC identity → Corbetti mesh TCP/6697 | Allow after the exact grant and binding exist | TLS verifies selected name and CA; SASL succeeds with that phone's account |
| Same identity → Corbetti TCP/22, 6667 and 18080 | Deny unless separately authorized | Bounded connection probes; existing POCO probe permission evaluated separately |
| Same identity → Jaguar TCP/5432 | Deny | Bounded probe; no DB credential issued to phone |
| Same identity → another peer's administrative or agent ports | Deny | Representative tests against the reviewed policy |
| Existing workstation/ThinkCentre access | Preserve | SSH recovery and IRC tunnel regression checks |
| Phone with revoked mesh enrollment | Deny private service access | New connections fail after revocation propagation |
| Valid mesh access with revoked IRC credential | Deny IRC authentication | Application-level rejection independent of network access |
| Valid IRC account → unauthorized channel/history | Deny | Membership and history tests with no OPER or channel-founder privilege |

Test IPv4 and IPv6 separately if the endpoint publishes both; do not infer one
from the other. Include Wi-Fi/mobile-data transitions, idle screen lock, delayed
notifications, reconnect and retained history. Measure memory and battery on
the POCO before repeating on the A02. A permanent agent executor is outside this
phone pilot; chat content does not grant command execution authority.

## Delivery order

1. Merge and verify the preparation/quarantine changes. Complete the recovery
   gate from [Headscale reconciliation](HEADSCALE-RECONCILIATION.md).
2. Choose name, trust/renewal path and identities. Prepare exact service bind,
   firewall, mesh policy, credentials, acceptance commands and rollback.
3. Obtain authorization for that concrete live change; apply one component at
   a time and verify existing access before continuing.
4. Provision the phone's revocable IRC credential through the existing vault
   workflow. Start with the proposed limited channel set in the
   [roadmap](../network/local/ROADMAP.md), subject to explicit membership review.
5. Connect the POCO for installation/debugging only when endpoint and account
   readiness are demonstrated. Record the acceptance evidence and repeat on
   the A02 only if needed.

This sequence does not depend on router administration, a ZTE factory reset,
LAN route advertisement, an exit node or placing PostgreSQL in Kubernetes.
