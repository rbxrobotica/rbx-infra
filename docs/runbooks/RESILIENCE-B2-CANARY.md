# B2 synthetic canary harness

Prepared 2026-11-08. This runbook publishes the bounded pilot procedure and its
offline tests. It does not record a completed live pilot or authorize a future
execution. Live results must be reviewed and recorded separately.

Keep real profiles, credentials, UI receipts, artifacts, execution receipts and
downloads outside this repository in an operator-owned 0700 directory, with
regular files 0600. Never commit them. The versioned source and tests contain
only synthetic fixtures and placeholder identities.

## Interface

`python3 -B scripts/resilience/b2_canary.py PHASE --profile /private/profile.json --receipt /private/receipt.json`

Phases, in order:

1. `authorize`: validates the scoped credential, account, bucket, prefix, exact
   owner-approved capabilities, expiry, expected origins and bucket metadata.
2. `control-upload --artifact /private/canary.gpg --ui-receipt /private/control-ui.json`
3. **Pause:** the authorized operator changes the dedicated bucket's default through its console to
   Compliance, one day, reads it back and writes a new UI receipt.
4. `locked-upload --artifact /private/canary.gpg --ui-receipt /private/locked-ui.json`
5. `download-and-verify --downloads-dir /private/downloads`: downloads both exact
   file IDs, verifies byte count, expected SHA-256 and version/name/SHA1 headers,
   and stores verified ciphertext as control.gpg/locked.gpg in an existing 0700
   directory, files 0600. The authorized operator can use its separate synthetic recovery key for a
   local GPG restore. The harness does not decrypt or establish application recovery.
6. `delete-control`: requires both verified downloads; deletes only control ID.
7. `attempt-delete-locked`: requires successful control deletion and verified
   locked download. Accepts only HTTP 401 with body status 401/code access_denied,
   then downloads that exact protected ID and verifies it still matches.
   Optional `--downloads-dir /private/downloads` stores locked-after-denial.gpg.

Each invocation reads credential JSON **only from a pipe on stdin**. The authorized operator must
produce it from the approved pass entry in memory; no credential file, command
argument, environment variable or shell tracing. The exact stdin schema is:

```json
{"keyId":"SCOPED_APPLICATION_KEY_ID","applicationKey":"SCOPED_APPLICATION_KEY_SECRET"}
```

The key ID is compared to profile.keyId before Basic authorization. It is **not**
the account ID; profile.accountId is compared separately to the v4 response.
All invocations reauthorize; no account or upload token is persisted. Run each
invocation under operator supervision and a 180-second external wall timeout.
No phase retries any request. A mutating phase persists an intent before issuing
an upload/delete and refuses reexecution, even after uncertain failure. Do not
remove this guard or change the prefix to retry an ambiguous write: the authorized operator first
reconciles the exact dedicated prefix/versions in its console.

## Exact private profile schema

Every field below is required, and extra fields are rejected. Values shown are
examples, **not real account configuration**. The authorized operator supplies observed identities,
the already confirmed account origins and the known synthetic GPG artifact hash.
keyCreatedAtUTC is derived from the observed native expiry minus the approved
86400 seconds, after the operator matched the expiry to the UI. It is not a claim of an
independent observation of key creation. Native expiry must be in the future
and no later than that declared creation time plus 24 hours.

```json
{
  "schema":"rbx.b2.canary-profile.v1",
  "accountId":"OBSERVED_ACCOUNT_ID",
  "bucketId":"OBSERVED_CANARY_BUCKET_ID",
  "bucketName":"actual-dedicated-canary-bucket",
  "prefix":"rbx-resilience-canary/actual-run-name/",
  "keyId":"OBSERVED_SCOPED_APPLICATION_KEY_ID",
  "keyCreatedAtUTC":"2026-11-08T12:00:00.000Z",
  "capabilities":["bypassGovernance","deleteFiles","listBuckets","listFiles","readBucketEncryption","readBucketLifecycleRules","readBucketLogging","readBucketNotifications","readBucketReplications","readBucketRetentions","readBuckets","readFileLegalHolds","readFileRetentions","readFiles","shareFiles","writeBucketEncryption","writeBucketLifecycleRules","writeBucketLogging","writeBucketNotifications","writeBucketReplications","writeBucketRetentions","writeBuckets","writeFileLegalHolds","writeFileRetentions","writeFiles"],
  "apiOrigin":"https://api005.backblazeb2.com",
  "downloadOrigin":"https://f005.backblazeb2.com",
  "artifactSha256":"0000000000000000000000000000000000000000000000000000000000000000",
  "artifactSizeBytes":12345,
  "syntheticOnly":true
}
```

This exact 25-capability set is the actual owner-approved UI grant, not a generic
RW assumption. Extra, unknown, omitted or duplicated capabilities fail. There is
no key management, listAllBucketNames or deleteBuckets capability. Despite the
grant's retention/configuration privileges, the harness implements **no** bucket,
retention, encryption, lifecycle, replication or legal-hold mutation endpoints
and never sends bypassGovernance, file-retention or legal-hold upload/delete
headers. Governance does not qualify: this canary requires Compliance.

## Exact authorized operator UI receipt schema

Required for **each upload**, with a new authorized operator observation no more than 15 minutes
old and never future-dated. IDs/prefix must match the profile. The locked receipt
must be newer than the receipt timestamp recorded after the control upload.

```json
{
  "schema":"rbx.b2.canary-ui.v1",
  "operatorAuthorized":true,
  "accountId":"OBSERVED_ACCOUNT_ID",
  "bucketId":"OBSERVED_CANARY_BUCKET_ID",
  "bucketName":"actual-dedicated-canary-bucket",
  "prefix":"rbx-resilience-canary/actual-run-name/",
  "observedAtUTC":"2026-11-08T12:02:00.000Z",
  "allPrivate":true,
  "keepAllVersions":true,
  "sseEnabled":true,
  "objectLockEnabled":true,
  "defaultRetention":{"mode":null,"days":null}
}
```

For locked-ui.json use `{"mode":"compliance","days":1}` instead. No token,
password, full upload URL or authentication header belongs in these receipts.
The human approved the bounded procedure; Codex may record actual console
readbacks under that authorization. This does not claim that the human personally
inspected each snapshot. Local observations are **not authenticated provenance**.
Readable native bucket/file retention must match; filtered metadata is UNKNOWN
and stops the phase. Native SSE must be readable and SSE-B2/AES256. Lifecycle
rules must be empty. Object Lock alone never counts as proof of protected data.

## Bounds and transport

Use one closed, encrypted **synthetic-only** GPG artifact prepared separately
with the isolated local fixture's temporary public-only producer and separate
recovery key. Its source and plaintext bundle must already have been removed.
Each upload is <=512 KiB, both combined <=1 MiB. Exact filenames are
`<prefix>control.gpg` and `<prefix>locked.gpg`; no generic list/purge/fallback.

Stdlib transport verifies TLS, ignores proxy configuration, refuses redirects,
uses timeout 15 seconds per request plus an enforced 175-second POSIX alarm,
has no retries, allows <=8 requests per
phase, bounds metadata to 64 KiB and downloads to the known artifact size. Upload
URLs must use the documented HTTPS pod-NNN-NNNN-NN.backblaze.com endpoint with
an exact v4 path, expected bucket ID and a single opaque cvt in either documented
path or query form. Account authorization is never sent to the upload URL; its
separate upload authorization token is used. No cloud SDK, pass reader, HTTP
debugging or ambient provider configuration is present.

The uploaded response's legalHold field is normalized to visibility and known
on/off/null values; other shapes become unknown, never raw diagnostics. A
null/missing value does not establish absence of legal hold.
The report does not establish a unique cause of a deletion denial.

The execution receipt is created atomically with 0600 and guarded by a nonblocking
local flock. It contains version IDs, fixed names, sizes, hashes, timestamps and
phase state, but no credentials/tokens/raw API bodies/full upload URLs. Preserve
the exact locked version for owner-console cleanup **after** its retention date;
do not attempt bypass or shortening. The scoped key expires around that date.

Sanitized stdout contains booleans, phase names and counts only. A successful
bounded canary does not qualify production backups, H1, recovery custody, a real
Headscale/PostgreSQL restore, scheduling, RPO/RTO or independent provenance.
Existing metadata-only simulation and shared S3/GetBucketPolicy clients are
unchanged. Unit tests use an in-memory transport and synthetic byte strings; they
do not represent a successful live canary.

## Offline tests

`python3 -B -m unittest discover -s scripts/tests -p "test_resilience_b2_canary.py" -v`

For the complete synthetic suite in the pinned rootless, network-disabled
runtime, run `bash scripts/resilience/run-local-fixture.sh`. It accepts no
arguments and has no live profile, credentials or writable host mount. The
existing workflow runs that same wrapper; it never invokes this canary CLI.

No real profile, credential, pass entry or network is needed by these tests.

## Reviewed source boundary

The initial published `scripts/resilience/b2_canary.py` is byte-identical to the
reviewed pilot source, SHA-256
`e6e10c2e60f66f8f1a24057119cd6dcdc55e217cf744b3ea9db2edd7fdece6e4`.
Its tests differ from the private pilot tests only in their module import. The
25-capability UI role is a bounded pilot observation, not a production
least-privilege recommendation. The separate metadata-only simulation profile
remains unchanged and does not qualify this read/write role.

The protocol requires separate, current operator authorization for its exact
account, empty dedicated bucket, prefix, short-lived credential, synthetic
artifact and one-day Compliance retention. No master key, production data,
shared bucket, policy bypass or automatic cleanup is part of this procedure.

Primary API references: [authorize v4](https://www.backblaze.com/apidocs/b2-authorize-account),
[upload URL](https://www.backblaze.com/apidocs/b2-get-upload-url),
[upload file](https://www.backblaze.com/apidocs/b2-upload-file),
[download exact version](https://www.backblaze.com/apidocs/b2-download-file-by-id),
[delete exact version](https://www.backblaze.com/apidocs/b2-delete-file-version),
and [Compliance versus Governance](https://www.backblaze.com/docs/cloud-storage-configure-immutable-storage-for-reseller-and-msp-customers).
The Native API version is v4; API reference pages were checked on 2026-11-08.
