# Synthetic local backup and restore fixture

Implemented: 2026-11-03. Scope: synthetic SQLite/application configuration,
real GnuPG public-recipient encryption and local ciphertext readback/restore.
This is a mechanism check, not H1 or production recovery evidence.

## Run with enforced isolation

Use a non-root account with local rootless Podman. The immutable runtime below
is the existing Comms backup image, whose provenance is recorded in
[Comms preservation](COMMS-PRESERVATION.md). It supplies Python and GnuPG;
this fixture does not invoke its database backup runner. If not already cached,
fetch the exact public image separately, without adding production credentials:

```bash
env -u CONTAINER_HOST -u CONTAINER_CONNECTION podman --remote=false pull \
  ghcr.io/rbxrobotica/comms-backup@sha256:fc4a6037f035baa0d229bd86f744ceca1ddb720e672004d0b7ffad1209365124
bash scripts/resilience/run-local-fixture.sh
```

The runner never pulls an image itself. It uses no network, a read-only root
filesystem, dropped capabilities, a non-root user, private namespaces and a
bounded disposable tmpfs. Only the new fixture code and its tests are mounted
read-only. It accepts no source paths, account settings, production credentials
or container-engine overrides. No cloud account or resource is needed.

The Python CLI can also run directly for development:

```bash
python3 -B -m unittest discover -s scripts/tests -p 'test_resilience_local_fixture.py' -v
python3 -B scripts/resilience/local_fixture.py
```

Direct Python execution does not enforce a network namespace. The CLI itself
has no provider or network transport, ignores inherited credential variables
for subprocesses, and uses explicit private GnuPG homes with key discovery
disabled. Do not describe the direct invocation as a network-isolated drill.

The [November 3 SQLite/GPG execution report](evidence/2026-11-03-resilience-local-fixtures/sqlite-gpg.json)
records 15 passing tests and the successful isolated roundtrip, including exact
stdout, test output, runtime digest and hashes of the source files executed.
This report records only the explicitly limited synthetic mechanism below.

## What is exercised

1. Generate two synthetic node rows and a reserved `.invalid` application URL.
   Capture SQLite through its backup API and record per-file sizes/hashes plus
   a logical-row digest in the manifest.
2. Generate an ephemeral synthetic recovery key. Import only its public key
   into a separate producer keyring and verify that the producer holds no
   private keys. The test key has no role outside this disposable fixture.
3. Package a fixed allowlist of regular files and encrypt the artifact. Copy
   ciphertext through a **local filesystem transport**, recording its hash.
4. Remove the original source and plaintext bundle. Decrypt into a fresh
   location, verify the captured manifest and all files before writing a
   restore, then check SQLite integrity, exact rows and application state.
5. Remove temporary files and request shutdown of the agents belonging to these
   temporary keyrings. Report successful GnuPG cleanup acknowledgement separately
   from removal of the temporary directory; either unconfirmed result makes the
   CLI fail. Cleanup failures do not mask a preceding capture/restore failure.
   The container's tmpfs disappears on exit. Unlinking files on a directly
   invoked host is not a promise of secure erasure.

Failure tests use real GnuPG for missing private keys, modified ciphertext and
excessive decompression. Archive tests reject unexpected/duplicate paths,
traversal, links, devices, oversized inputs, changed manifests/files and invalid
SQLite state; failed verification leaves no restored output. Existing output is
never overwritten. Raw subprocess diagnostics and inherited credentials are not
printed. No cloud client or cryptographic operation is mocked in the successful
roundtrip; failure-redaction and cleanup-reporting tests use explicit mocks.

## Limits and next gate

The manifest/ciphertext hashes are captured and held by the same fixture
process, and public-key encryption here is unsigned. These checks do not prove
who authored the data, its freshness, or resistance to joint substitution of
both ciphertext and reference hashes. A trusted, independently preserved receipt
and authenticated provenance remain separate requirements for real backups.

The JSON report explicitly marks synthetic data, local-copy transport and
unperformed provider, retention and Headscale runtime tests. Separate keyrings
on one machine do not establish independent physical custody or recovery by
another person. No PostgreSQL backup, real Headscale schema/identity, B2 upload,
Object Lock, retention policy, scheduled capture, RPO/RTO commitment, DNS or mail
change is established here. The existing independent PostgreSQL fixture is
documented in Comms preservation. Its [separate November 3 execution report](evidence/2026-11-03-resilience-local-fixtures/postgres16.json)
records a real PostgreSQL 16/GPG synthetic restore with a stubbed S3 transport;
it does not demonstrate a B2 connection or production database recovery.

Keep production capture and provider integration as separate, bounded steps.
In particular, the current shared S3 client's bucket-policy preflight is not
automatically portable to ordinary B2 accounts: Backblaze's
[GetBucketPolicy availability](https://www.backblaze.com/apidocs/getbucketpolicy)
is limited to the Enterprise Web Console. Do not bypass a failed security check
or silently fall back to the existing Contabo destination. This increment does
not modify that client or broaden the Comms database boundary.

The separate [B2 metadata simulation](B2-METADATA-SIMULATION.md) adds offline
contract tests to this wrapper. They use injected fixtures only, with no cloud
transport, and do not change the scope of the SQLite/GPG roundtrip report.

Execution reports describe only the code revision actually run. Preserve
sanitized results in rbx-resilience if useful, without promoting component
status or treating a local fixture as a passing provider-exit drill.
