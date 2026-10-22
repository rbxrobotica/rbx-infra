# Native DNS-01 boundary fixture

Run from the repository as a non-root user:

```bash
bash scripts/network/test-irc-dns01-native.sh
```

The wrapper builds a dedicated image using a digest-pinned Ubuntu 24.04 base
and packages from Ubuntu's signed repositories. Package revisions are recorded
in the result, not fully frozen; runtime fails unless PowerDNS is 4.8.3. This
is not a byte-reproducible package snapshot. Builds use network access only to
obtain public packages; no host credentials are injected.

Runtime uses local rootless Podman, a read-only root, no capabilities, no external
network, no published ports or host mounts, and bounded resources. PostgreSQL
and PowerDNS run as UID 10001. All database, configuration, random TSIG material
and logs are in disposable tmpfs. Loopback DNS exists only within the container.
The image contains fixture code and the non-secret candidate plan, not test keys.

The runner verifies the candidate's global and per-zone policy shape and records
its SHA-256. Domain names are mapped to `.example`, the authorized source to
127.0.0.1, and an unauthorized source to 127.0.0.2. It uses the same native
PowerDNS 4.8.3/gpgsql authorization controls with a fresh PostgreSQL 16 database.
No real domain or address is contacted. The metadata NOTIFY setting is checked
in the candidate but secondary replication is deliberately not simulated here.

The matrix covers signed TXT add/delete, unsigned requests, wrong secret, wrong
source, parent and sibling zone writes, and a forged child-zone request targeting
a parent-zone owner. Each refusal is followed by a readback proving no TXT was
created. Final successful add/delete proves that negative cases were not merely
caused by an unavailable daemon. No timeout counts as an accepted refusal.

The wrapper returns sanitized JSON only. Transient build/runtime logs are
discarded; failures return nonzero. A developer can reproduce a synthetic
failure directly in the fixture container without introducing real secrets.
The engine's local image/build cache remains for subsequent runs. Remote Podman
environment overrides cannot redirect the harness.

This proves the tested primary authorization behavior, not live deployment,
delegation, DNSSEC/CAA, authoritative propagation, Eagle secondary transfer,
ACME issuance, certificate deployment or Android IRC connectivity.
