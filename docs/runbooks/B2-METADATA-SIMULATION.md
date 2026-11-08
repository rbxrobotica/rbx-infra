# B2 Native metadata contract simulation

Prepared: 2026-11-07. **Simulation only. No real B2 account is qualified.**
There is no HTTP transport, credential loader, upload, object deletion or
retention mutation. The CLI accepts only `--simulate`; it cannot consume account
settings or production paths. Existing Comms/Plausible clients and their
GetBucketPolicy checks are unchanged.

## Contract and source version

This increment models metadata responses from **B2 Native API v4**, using the
official authorize/list-buckets documentation dated September 11, 2026 and
reviewed November 7. Authorization restrictions are read from
`apiInfo.storageApi.allowed`: `buckets` contains `id`/`name` pairs alongside
`capabilities` and `namePrefix`. The older v3 restriction shape is not accepted.
The native bucket response reports its identity, privacy and permission-filtered
File Lock configuration. Missing visibility is not evidence that a setting is
disabled or safe. Sources:

- [b2_authorize_account v4](https://www.backblaze.com/apidocs/b2-authorize-account)
- [b2_list_buckets v4](https://www.backblaze.com/apidocs/b2-list-buckets)
- [Application key restrictions](https://www.backblaze.com/docs/cloud-storage-application-keys)

The tool defines a deliberately narrow **metadata-only** profile:

- Explicit account ID, one exact bucket ID/name, and an exact restricted prefix
  `rbx-resilience-canary/<run-name>/`. Unrestricted, sibling or broader prefixes
  fail. Names and identity syntax are a conservative tool policy, not a complete
  definition of all valid B2 names.
- Exactly `listBuckets` and `readBucketRetentions`. Extra privileges, missing
  capabilities, duplicates and unknown capability names fail. These privileges
  cannot upload or restore objects and are not a future producer/recovery role.
- Explicit canonical HTTPS origins for native metadata, download and S3. Each
  must match both the supported host syntax and the expected profile. There are
  no defaults or Contabo fallback. Origins must eventually come from verified
  provider/account configuration; no region is inferred from native cluster IDs.
- One exact bucket result, `allPrivate`, readable File Lock metadata and File
  Lock enabled. This initial profile requires no default retention and no
  lifecycle rules, plus an explicit integer configuration revision. An existing
  retention schedule or expiry rule requires a separate reviewed profile.

The sample account/bucket IDs and name are explicitly invented. The origin URLs
use the official documentation's US West examples; they do not select an actual
RBX region or replace the proposed US East choice. No administrative inventory
record is populated from these fixtures.

## Execute offline

```bash
python3 -B -m unittest discover -s scripts/tests -p 'test_resilience_b2_preflight.py' -v
python3 -B scripts/resilience/b2_preflight.py --simulate
bash scripts/resilience/run-local-fixture.sh
```

The existing wrapper runs both the SQLite/GPG fixture tests and these simulated
B2 contract tests inside its pinned, network-disabled container. Its stdout
retains the SQLite roundtrip report; invoke `--simulate` separately to obtain
the B2 simulation report. The CI workflow includes both suites without cloud
credentials. No provider access is needed or attempted by either module.

`MetadataTransport` is an injectable test seam; `FixtureTransport` returns only
in-memory encoded snapshots. No concrete network adapter is included. Responses
are bounded to 64 KiB, must be successful JSON objects and reject duplicate JSON
fields. Endpoint/auth-scope validation occurs before the simulated restricted
bucket lookup. Provider diagnostics, tokens and identifiers are excluded from
reports and fixed error codes; authorization tokens are not consumed or stored.

## Limits and next increment

A matching injected response is not a live privacy check, successful login or
configuration attestation. File Lock enablement is not proof that an object has
retention, and permission denial alone cannot prove WORM. Metadata can be stale,
forged or drawn from the wrong account; this simulation establishes no provenance.
The report always marks simulation mode, real qualification false and all live
access, anonymous denial, upload/restore and retention enforcement untested.

Provider integration remains a separate increment with bounded calls, approved
real identifiers, independent receipt custody, live privacy checks and an exact
dedicated fixture/canary scope. Encryption/readback already has its separate
[local fixture](RESILIENCE-LOCAL-FIXTURE.md); it is not connected to B2 here.
Do not disable the existing AWS/S3 checks to make this simulation look like a
working archive backend. No status record, passing recovery drill, RPO/RTO or
production capability is established by these tests.
