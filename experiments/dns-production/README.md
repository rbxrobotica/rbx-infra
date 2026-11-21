# Production DNS qualification

This directory adds read-only qualification tools; it does not deploy the
synthetic signer into production or change any zone, DS, NS, secret or account.

## Public corpus audit

`corpus.json` pins the ten inventoried production zones, their current authority
names and the five zones already intended to be signed. Missing DS cannot
silently turn a required signed zone into a passing unsigned observation.

```sh
rbx-build-cache run -- python3 -B -m unittest discover -s experiments/dns-production -p 'test_*.py'
rbx-build-cache run -- python3 -B experiments/dns-production/audit_public.py \
  --timeout 3 --workers 2 --output /absolute/primary-ssd/evidence/new-public-audit.json
```

Requires dnspython with DNSSEC support and the installed Ubuntu BIND `delv`.
The output file must not exist; previous observations are never overwritten.
No package installation, credential lookup or production mutation occurs.
Source/evidence stay on the primary SSD. Build-cache leases cover recreatable
test outputs; no existing build paths are migrated.

The audit compares NS/DS from two recursive transport providers, discovers
the enclosing parent zone and queries two actual parent authorities over
UDP/TCP. It then queries the advertised authorities over IPv4/IPv6 and UDP/TCP,
compares SOA serial and NS sets, and authenticates DNSKEY against parent DS
before accepting signed SOA data. Recursive UDP loss can use TCP; a direct
authority's UDP failure is retained rather than hidden by TCP success.
Non-global authority addresses are not queried.

Fresh `delv` processes independently validate the root chain through each
recursive transport source using the installed validator's default root anchor.
AD flags alone are not accepted as validation. `unsigned answer` is recorded
as insecure, never secure; it fails a zone whose signed intent is pinned.
The installed validator version and binary hash are recorded. An Unbound-format
root-key file must not be supplied as a BIND configuration anchor to `delv`.

The DNS names, public addresses, DS, SOA serials and observation hashes are
public. No private signing key, account data, recovery value, zone body, API
token or complete signature is captured. Query concurrency and timeouts are
bounded. Full-zone content consistency, transfer authentication, independent
network monitoring, denial-of-existence correctness and provider-outage
survival are separate acceptance gates. `production_ready` stays false by
design even if the public checks pass.

## Production release gates

The corresponding resilience qualification record is the acceptance register.
Before any production operation it must establish current external backups,
tested restoration of actual production content/keys and remote state custody.
Account MFA/recovery, compatible authenticated transfers, provider-independent
parent delegation, original DNSSEC trust-chain preservation, independent
monitoring and complete disaster recovery/serial reconciliation remain explicit
gates. The undelegated NSD fixture and owner-only signer recovery cannot satisfy
these gates for the production PowerDNS corpus.

Prepare the exact IaC diff and rollback procedure before requesting the
production operation. Use the current canonical source and remote backend;
never import or apply over a local state file. The agent must not merge or
apply a production change under the generic qualification authorization.
