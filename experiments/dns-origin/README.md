# Zero-cost isolated DNS origin experiment

Authorized scope: the owner requested an isolated origin, transfers, DNSSEC and
origin-failure checks at USD 0. This temporary test serves only
`cloudns-xfr-pilot.rbxsystems.ch.`; no production delegation, signing settings,
database records, existing DNS daemon or paid resource is part of the operation.

## Preflight and reconstruction boundary

| Field | Evidence / declared operation |
| --- | --- |
| Affected state | New synthetic ClouDNS secondary, public signed fixture snapshots, one bounded transient NSD process and source-specific runtime TCP/UDP 53 rules |
| Source of truth | This feature branch for the executable source/config; resilience PR11 for observations; provider panel for trial state |
| Remote location | GitHub feature branches in rbx-infra and rbx-resilience; existing encrypted credentials remain in their verified private remote |
| Backup | Public synthetic inputs and signed snapshots are published to Git before provider transfer; temporary package binaries can be re-downloaded by exact package hash |
| Restore | Rebuild and local NSD parsing/reload must pass before remote service startup; provider restoration is a measured experiment, not assumed |
| RPO/RTO | RPO: last published public snapshot; target origin reconstruction 15 minutes; observed RTO not yet established |
| Owner | Leandro; root executor controls staging, firewall changes, outage and teardown |
| Local state | Cache only: downloaded official packages, extracted tools, logs and public zone copies; no production data or private signing key is copied |
| Budget | USD 0 new orders/paid usage; use existing resources and the active seven-day Premium M trial only |

Read-only inspection found existing DNS authorities occupied on both IP families.
Jaguar has free public TCP/UDP 53, Ubuntu 24.04, systemd 255, approximately 21 GiB
available memory and ample `/run` tmpfs. Its active UFW currently denies incoming
53. No trial-origin service is running there yet. Tiger SSH could not be verified
against existing local host keys; no host-key bypass or key replacement is used.

The origin uses official Ubuntu NSD 4.8.0 packages extracted into a private task
directory under `/var/cache/rbx-dns-origin-source`, without installing packages
or editing host service configuration. `/run` is mounted noexec and is retained
only for writable transient state/control; its protections are not relaxed.
NSD is authoritative-only. Run it as a transient DynamicUser with only
CAP_NET_BIND_SERVICE, NoNewPrivileges, strict filesystem protection, inaccessible
production data paths, 128 MiB memory and 10% CPU limits, and a hard runtime cap.
Expose only the exact test bind address/port to the provider transfer IPs and a
bounded tester address. Use a dedicated commented runtime firewall chain; never
flush/restore shared firewall rules or edit persistent UFW policy. Teardown
removes only the task-owned jump/chain, process and public staging directory.

The old shared-host services are outside the affected data state. Establish and
recheck their service/port baseline around this isolated process. Stop the NSD
process, never the host, database, k3s or production DNS, to inject origin failure.
The origin shares a host/provider with RBX workloads: this is process and fixture
isolation, not an independent infrastructure failure domain.

## Declared checks

1. Parse public fixtures with NSD and reconstruct them locally; preserve their
   hashes before any remote transfer.
2. Transfer the complete pre-signed zone to the real ClouDNS secondary, comparing
   normalized contents and serials. Confirm unsigned/unauthorized source AXFR is
   explicitly refused while healthy authorized controls still work. TSIG support
   must be established separately; IP ACLs are not cryptographic authentication.
3. Publish phase 2 and require convergence within 300 seconds. Keep failed or
   delayed observations; never rewrite expectations to match a provider.
4. Validate signed answers in an isolated Unbound resolver with the independently
   pinned synthetic trust anchor. Require valid data to produce AD, and tampered,
   expired and wrong-DS scenarios to fail validation. No system-clock changes.
   This lab anchor does not establish a public parent-to-child DNSSEC chain.
5. Stop the actual origin TCP/UDP listeners. Verify failure at the origin and
   retained correct answers at the secondary before the 120-second SOA expiry.
   Observe behavior beyond expiry and signature deadlines honestly. Do not count
   stale answers as current data or a short outage as expiry enforcement.
6. Recover the origin from the published public snapshots, verify convergence,
   then stop and remove the task-owned origin/network rules. Record residual
   trial zones and missing criteria before ending the task.

`build-fixtures.py` creates one test-only Ed25519 signer in memory and writes only
public snapshots/anchors. It never exports the private key. The snapshots expire
after one hour; repeating the lab re-keys and changes the lab trust anchor.
This is not a signing-key recovery drill or production custody model.

The first local run accepted the recently expired fixture under Unbound's
default signature clock-skew allowance. Preserve that failed receipt. A separate
strict-timestamp run explicitly sets both `val-sig-skew-min` and
`val-sig-skew-max` to zero, without changing system time. Its successful expiry
rejection would establish only this stricter lab configuration; it must not be
reported as the default resolver behavior.

## Executed observations

The strict local NSD/Unbound integration completed in 132.890 seconds: complete
54-record transfers, explicit unauthorized refusal, phase updates, signed
positive/negative/wildcard validation, tampered/expired/wrong-DS rejection,
actual origin shutdown, retention before SOA expiry, withholding after expiry,
and reconstruction. All owned process groups were removed. The failed default
skew attempt remains in resilience evidence.

The real temporary origin and ClouDNS secondary preserved the original SOA
timers. Phase 2 converged across four IPv4 authorities over UDP/TCP in 6.494
seconds. `probe-managed-secondary.py` passed 184 exact signed RRset checks and
20 cold Unbound cases using the independently pinned lab anchor.

The ClouDNS UI export omits RRSIG and NSEC: only 22 of the full 54 records appear
in that export. It is not a complete signed recovery copy. Complete public
source snapshots and immutable receipts are published in resilience PR11.
The real managed secondary retained exact phase-2 answers at 125 and 150 seconds
despite SOA EXPIRE 120. Availability before expiry passed; the declared
withholding/freshness boundary failed. The provider's supported minimum and stale
policy are unknown; this is not evidence that it never expires a zone. Origin
process reconstruction from cached public inputs and sampled authority recovery
took 6.467 seconds, not a full infrastructure/identity/signing recovery RTO.

The safety cleanup removed the actual origin process, executable cache, runtime
control state and exact firewall chain/jumps. Final inspection found no public
53 listener and the same active PostgreSQL/k3s-agent units. The task-owned SSH
agent was stopped. Provider test zones remain for review without parent
delegation or production data. Later direct replies still included expired
fixture signatures; strict cryptographic validation rejected them.

These component results do not pass H-dns. Preserve failed receipts and keep
freshness policy, complete signed backups, TSIG, IPv6/multi-vantage behavior,
independent discovery and signing/publication recovery as explicit gaps before
any production migration or paid continuation.

Reproduction requires the official extracted package hashes in
`package-pins.json`, new public fixtures/anchors and a fresh work directory:

```sh
python3 -B experiments/dns-origin/build-fixtures.py --output <new-public-fixtures>
python3 -B experiments/dns-origin/run-local-lab.py --tools-root <extracted-tools-root> --fixtures <new-public-fixtures> --work-dir <new-local-run-directory>
python3 -B experiments/dns-origin/probe-managed-secondary.py --tools-root <extracted-tools-root> --fixtures <published-managed-fixtures> --work-dir <new-managed-probe-directory>
```

Review/rebind the actual origin, partner addresses, fixture validity and test
ports before repeating remote operations. No script authorizes production NS/DS,
global transfer ACLs, paid resources, host package installation or live outages.

The production parent remains Contabo-dependent, and neither production delegation
nor full H-dns provider-exit qualification is claimed by these component tests.
