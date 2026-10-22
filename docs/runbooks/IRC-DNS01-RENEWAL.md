# Scoped IRC DNS-01 issuance and renewal candidate

Status: preparation only, October 18, 2026. No production DNS update, key
creation, CA registration, certificate issuance, installation or timer activation.

## Observed starting point

Read-only access through the trusted ThinkCentre SSH path found Pantera running
PowerDNS 4.8.3 with `gpgsql`, primary mode, and default/commented `dnsupdate=no`.
The current parent zone has no `ALLOW-DNSUPDATE-FROM` or `TSIG-ALLOW-DNSUPDATE`
metadata. No dedicated challenge zone appeared in the zone inventory. This is
not an audit of every zone's metadata. The existing Terraform API credential
reference is `pass:rbx/dns/pdns-api-key`; its value was not read or copied.

## Recommended candidate

Use the existing Pantera/Eagle authoritative pair and delegate only
`_acme-challenge.irc.rbxsystems.ch` as a child zone. This avoids a new VPS or DNS
proxy process. `infra/irc/acme/dns01-plan.json` contains the concrete candidate
names, addresses and metadata, but is not consumed by production automation.

Create the child on Pantera and replicate it to Eagle, with NS records in both
the parent delegation and child apex. Confirm child SOA, transfers, notification,
serial increments and public authoritative visibility before issuance. Never
assume that the existing BIND secondary automatically discovers a new zone.
Check DNSSEC/DS state and CAA before delegating; an incorrectly signed delegation
must not be bypassed. Confirm the issuer's actual egress address before applying
the candidate Corbetti source restriction `13.140.148.30/32`.

PowerDNS needs global dynamic updates enabled, an explicitly empty global
source allowlist, and a source allowlist plus TSIG requirement on the child only.
Audit every zone first: broad pre-existing per-zone allowances would become
active when the global feature is enabled. Do not use the newer
`dnsupdate-require-tsig` option on observed 4.8.3; it is a 5.x feature. Do not add
a custom Lua policy, which replaces the normal authorization checks.
[PowerDNS dynamic update authorization](https://doc.powerdns.com/authoritative/dnsupdate.html).

The child-scoped key permits changes within that child, not only one TXT RRset.
It cannot be described as a single-record capability. Compromise can disrupt the
challenge zone and potentially authorize certificates for `irc.rbxsystems.ch`
and `*.irc.rbxsystems.ch`; protect it accordingly. Never associate the key with
the parent or other zones. The runner requests only the exact non-wildcard IRC
name. The proposed secret reference is `pass:rbx/irc/acme/rfc2136-tsig` and does
not claim that an entry already exists. Generate/import without logging secret
output or putting it in command arguments; no key was generated here.

## Pinned issuer and runner

The candidate uses upstream Lego 4.35.2 linux/amd64. Archive digest was checked
against both the GitHub release digest and upstream checksum manifest; the
extracted executable digest is recorded in `infra/irc/acme/lego-provenance.json`.
This establishes checksum provenance, not an independent publisher signature or
a security/support guarantee. Review security advisories before deployment.

The downloaded Lego 5.5.2 has a different command interface and its `dnshelp`
reported RFC2136 unsupported. The 4.35.2 binary accepted the generated issue and
renew command forms with `--help` and exposes this provider's `DNSUPDATE_*`
environment settings. Native help validation is not an issuance test. Provider
configuration was also checked against the versioned source:
[Lego 4.35.2 RFC2136](https://github.com/go-acme/lego/tree/v4.35.2/providers/dns/rfc2136).

`scripts/network/irc_acme.py` defaults to a credential-free plan. `--execute`
requires a caller-supplied verified binary SHA-256, private existing state and a
private TSIG payload file. It rejects an empty/invalid key, separates staging
from production state, clears inherited provider configuration, serializes runs
and limits subprocess duration. Provider stdout/stderr is suppressed to avoid
credential-bearing error output; a failure needs a separate controlled diagnostic
session, not automatic retry with verbose logs. Successful ACME execution does
not mean renewal happened or the deployed certificate changed.

Example preparation-only invocation:

```bash
python3 scripts/network/irc_acme.py \
  --lego /opt/rbx/irc-acme/lego \
  --state /var/lib/rbx-irc-acme \
  --email operator@example.test --environment staging --operation issue
```

Replace the example contact with the approved RBX recovery contact. Execution
also needs `--execute`, `--sha256` from the provenance record and `--tsig-secret`
pointing to a private file containing only the base64 key payload. Run as a
dedicated unprivileged issuer account; state/environment subdirectories must
already exist with mode 0700 and credentials with 0600 (or systemd credentials).
Do not put the key in shell history, an environment file, Terraform state or Git.

`infra/irc/acme/renew.service.example` and `renew.timer.example` are inactive
examples for twice-daily checks, with systemd credential delivery and a pinned
binary digest. The non-secret `operator.env` contains `RBX_ACME_EMAIL` only.
Install only after confirming host systemd compatibility, account ownership,
paths and staging acceptance. The timer renews existing production material;
initial issuance is a separate explicit action. Nothing copies keys into Ergo
or reloads it. Certificate/key pairing, trusted chain, atomic deployment,
reload verification and deployed-expiry alerting remain a separate prerequisite
before this can be called end-to-end renewal automation.

## Acceptance before activation

1. Restore-tested DNS/Ergo backup, scoped key custody and owner confirmed.
2. In an isolated authoritative fixture, valid signed child TXT add/delete
   succeeds; unsigned, wrong-key, wrong-source and parent/sibling updates fail.
   Test both parent-zone and forged child-zone UPDATE packets. Public child
   answers must agree across Pantera/Eagle after add and cleanup.
3. Apply the reviewed DNS change only with exact operator approval. Repeat bounded
   probes with disposable labels; never use real parent records as negative tests.
4. Complete staging issuance and renewal without disabling propagation checks.
   Ensure cleanup works after a failed attempt. Then authorize production issuance.
5. Validate trust/name/key pairing, deploy the pair atomically and verify Ergo's
   served certificate and desktop regression before granting the phone TCP/6697.
6. Test renewal scheduling, failure notification, deployed-expiry alerts and
   recovery. Define the named owner and alert destination before enabling timers.

Rollback first disables the new timer and revokes only this child's update
authorization. Restore prior DNS settings/delegation from the reviewed snapshot;
do not disable updates globally if another authorized consumer was added later.
Keep existing desktop IRC and valid certificates in place. An issued certificate
is not automatically revoked by removing the challenge key.

## Validation and remaining gates

October 22: the isolated primary acceptance gate now has a reproducible native
fixture in `scripts/network/test-irc-dns01-native.sh`, also run by CI. Ten cases
passed with PowerDNS 4.8.3/gpgsql and PostgreSQL 16: signed add/delete, six
refusals including wrong-source and parent/sibling attempts, then final positive
add/delete. Readbacks confirmed no forbidden TXT records. The sanitized
[evidence](../network/local/evidence/irc-dns01-native-2026-10-22.json) records
the candidate hash and package versions. No production DNS or credentials were
used. See the [fixture scope](../../scripts/tests/fixtures/irc-dns01/README.md).

Eight runner tests and six endpoint preflight tests pass with synthetic data.
Native Lego CLI/provider discovery passed. RFC2136 writes have now been tested
only inside the synthetic fixture; no production update, ACME request or systemd
activation was attempted. Secondary provisioning/replication, scoped real key
creation and the certificate deployment hook remain open gates, explicitly not
evidence of production readiness.

Secret review: references only in the candidate manifest and inactive service;
no real secret was read, copied or committed. Tests contain synthetic key material.
The audit skill's referenced output template was absent; this paragraph records
the available findings without inventing a completed template check.
