# Synthetic Headscale capture and restore fixture

Prepared: 2026-11-08. This increment exercises a local capture/restore mechanism
using generated data only. **Production capture remains NOT IMPLEMENTED.** The
[first real backup contract](HEADSCALE-FIRST-REAL-BACKUP.md) still governs the
current baseline, consistency, archive identities, custody and real restore
gates. A passing fixture does not clear those gates.

The user confirmed that the GPG recovery copy needs preparation and checking.
No offline recovery-copy verification, independent custody or recovery by
another person is established by this document or the temporary fixture keys.

## Boundary and execution

[`headscale_fixture.py`](../../scripts/resilience/headscale_fixture.py) has no
source-path, host, credential, provider or key arguments. Its CLI accepts no
arguments. All state and temporary encryption keyrings are
generated inside its disposable working directory. It must never be pointed
at Eagle, an existing archive, a password store or a real Headscale database.

Use the existing enforced sandbox from the repository root:

```bash
bash scripts/resilience/run-local-fixture.sh
```

The wrapper requires rootless local Podman and an already cached immutable
runtime. It enforces network isolation, a read-only root filesystem, dropped
capabilities, an unprivileged user, bounded tmpfs/resources and read-only mounts
of fixture source/tests. Host homes, SSH/GPG agents, production files, password
stores and cloud credentials are absent. It performs no implicit image pull.
The image supplies the toolchain; its production database runner is not invoked.

Direct development execution does not enforce a network namespace:

```bash
python3 -B scripts/resilience/headscale_fixture.py
```

Do not describe that direct command as an isolated drill. Neither invocation
reads or backs up a real service. The existing generic SQLite/GPG fixture and
the [B2 synthetic canary](RESILIENCE-B2-CANARY.md) retain their separate scopes.

## Mechanism under test

1. Generate a small SQLite database in WAL mode, plus fixed configuration,
   policy and service-identity/TLS fixture files. The schema and values are
   synthetic; they are not the Headscale 0.29.3 schema. Noise/TLS/conditional
   DERP file bytes are dummy state, not operational cryptographic identities.
2. Capture the committed SQLite state through the online-backup API into a new
   snapshot. Do not use a bare copy of the live fixture database/WAL as the
   export. Select a fixed, bounded set of regular files and detect observed
   source changes; unexpected paths and unsupported file types must fail.
3. Build a manifest with declared file names, sizes and hashes, capture interval
   and deterministic application checks. Package the closed export without
   recursive extraction or arbitrary archive paths.
4. Reuse the existing GPG helper to generate a temporary recovery key and import
   only its public key into the producer keyring. Encrypt the closed artifact
   with that public recipient and verify that the producer has no private key.
5. Copy ciphertext through a local filesystem transport, record its expected
   hash and remove the generated source and plaintext export before recovery.
   This source destruction is fixture-only; real sources must remain intact.
6. Read back and decrypt the copied ciphertext using the separate temporary
   recovery keyring. Supply the expected manifest hash separately from the
   archive. Validate all members, bounds and hashes before writing a new private
   restore, then check SQLite integrity and the declared application queries.
7. Remove temporary working files and request shutdown of the temporary GPG
   agents. Report failures honestly. Unlinking plaintext does not demonstrate
   secure erasure; the sandbox's disposable tmpfs bounds this fixture's storage.

The fixed exported files are `config.yaml`, `policy.hujson`, `noise.key`,
`tls-account.json` and `state.sqlite`, plus `derp.key` only for the embedded-DERP
fixture profile. `manifest.json` is the only additional archive member. The CLI
uses the profile without DERP; the module's synthetic test API can exercise both.
Configuration is JSON within the YAML subset, with logical fixture references.
The TLS member is a dummy account JSON object, not a certificate/private key or
an ACME cache captured from a server.

| Bound | Implementation contract |
| --- | --- |
| Source/export member | At most 512 KiB per file; regular, owned, single-link files with mode 0600 in a 0700 fixture directory |
| Captured payload | At most 1 MiB across the exported files |
| Archive/ciphertext | At most 2 MiB for each bounded read/artifact; uncompressed fixed-member tar |
| Capture progress | Five-second deadline checked during SQLite backup progress and capture boundaries; the sandbox supplies the enclosing execution/resource limits |
| SQLite model | One synthetic user and two related synthetic nodes; integrity, foreign keys, schema/model hashes and deterministic query results |
| Runtime API | Python 3.11+ with `sqlite3.Connection.serialize()`; the snapshot is serialized from the in-memory online backup |

`capture(source)` returns bundle bytes and the separately held manifest hash;
`restore(bundle, expected_manifest_hash, destination)` validates before creating
its new output. The manifest records the synthetic schema marker, DERP profile,
capture interval, per-file size/hash/mode and model verification values. Live
WAL/SHM files are allowed only as source-side SQLite companions, not exported
members. This is an API for the generated `Source`, not a supported arbitrary
source-directory interface.

## Executed checkpoint: November 8, 2026

The primary agent ran `bash scripts/resilience/run-local-fixture.sh` in the
pinned rootless runtime after the final test changes. All **88 tests completed:
87 passed and one existing inventory YAML test explicitly skipped**, because
this immutable image lacks PyYAML. The existing YAML-enabled CI job separately
requires that parser and runs all inventory tests. The new capture suite has
27 tests and no skip. Both generic and Headscale-shaped encryption/restore CLIs
then exited zero inside the same network-disabled container.

The Headscale-shaped report recorded a 20,818-byte ciphertext, five restored
files, two related nodes, SQLite integrity `ok`, source removal before recovery
and confirmed temporary-file/agent cleanup. Native Headscale, production data,
provider integration and independent custody flags remained false.

The executed source SHA-256 values were:

- `headscale_fixture.py`: `ea55ddfc724ced0014127bb320830acc0875911c63d5a530fe2fd6692dc21a67`
- `test_resilience_headscale_fixture.py`: `66b5e58c9d64e74c747b4f25ef16ead1f02b0b762cfdd40045ba9d6a612c7583`
- `run-local-fixture.sh`: `9c6260c6bc9b99dce2c3ed123e7badd301b30c58860ba1b92ce3ae1979adaf62`

These are dated synthetic execution observations, not production acceptance or
an offline recovery-key test. No real key was exported and no cloud operation
or production mutation was performed in this increment.

A successful report uses schema
`rbx.resilience.headscale-shaped-fixture.v1`, marks `synthetic_only: true` and
`transport: local-copy`, and reports restored node/file counts, integrity,
relationships, model and configuration/policy/identity-byte checks. It explicitly
keeps `production_data_accessed`, `production_capture_implemented`,
`independent_custody_tested`, `provider_integration_tested` and
`headscale_runtime_tested` false. `source_removed_before_restore` and the GPG/file
cleanup acknowledgements are separate observed conditions. No report or passing
test result is asserted merely by preparing this guide; evidence must identify
the exact source revision and actual invocation.

## Interpretation and failure conditions

SQLite's online backup supplies a database snapshot. Fingerprint/hash rechecks
on other files detect observed changes; they do not create a globally atomic
snapshot, exclude change-and-revert races or coordinate a real daemon and
certificate renewal. This fixture has no real concurrent administrative writer.
The production contract still needs a verified quiet window or equivalent
consistency method over all included state.

The database backup opens the generated database by its source pathname;
non-database capture reads use an opened directory descriptor and no-follow
checks. Neither arrangement should be generalized into an approved live-source
exporter without its own path/race, writer-coordination and failure review.

The expected manifest hash is external to the archive, but this fixture creates
and holds it in the same process. Its encryption is not authenticated backup
provenance. It does not establish authorship, freshness, independent receipt
custody or resistance to jointly substituting ciphertext and reference hashes.

Failures include unrecognized/duplicate members, traversal or links, out-of-bounds
input, observed drift, changed manifest/content, incorrect application state,
missing decryption material and failed cleanup. A matching ciphertext hash alone
is insufficient; restoring the captured application state is a separate check.
Existing output must not be overwritten, and failed validation must leave no
accepted restore behind.

No native Headscale executable, actual schema, runtime initialization, policy
engine, TLS/ACME, DERP, enrolled clients or service health is exercised here.
No B2/S3 request, Object Lock enforcement, production archive role, recurring
capture, alert delivery, RPO/RTO or H1 result follows from this local fixture.
The shared storage clients and their security checks remain separate.

## Recovery kit prerequisites before real data

These are acceptance requirements to prepare and verify, not assertions about
the current workstation or a completed secret export. Keep all material and
private location references outside Git; do not put secret-export commands in
this runbook or ask someone to paste secret values into chat.

| Required item | Evidence needed before a real recovery claim |
| --- | --- |
| Encrypted credential-store replica | Identified canonical source and durable private encrypted replica; dated reconciliation/open test using the intended recovery path |
| Decryption capability | Correct private decryption key/subkeys for the archive and required encrypted credentials, their fingerprint references and any required passphrase; verify a controlled decryption rather than relying on a public-key export, a cached agent or a file name |
| Independent access | Recovery identities and MFA/recovery factors usable without Contabo services, RBX mail/DNS/SSO or the source host; document provider recovery and revocation/rotation dependencies |
| Offline custody | Selected disconnected medium/location, authorized custodians, integrity and physical handling, plus an actual dated open/decryption test from the intended recovery environment |
| Instructions and toolchain | Readable offline procedure, compatible verified tools/packages, recipient/key mapping and trusted manifest/receipt references available without the producer |
| Lifecycle and accountability | Owner, verification interval, replacement/revocation procedure and coverage of retained archives when recipients or credentials change |

An encrypted credential-store replica and the private GPG recovery material are
separate dependencies. The kit must demonstrate that both can actually be used
under the declared failure scenario. Temporary public/private keyrings on one
machine establish neither independent locations nor a second custodian. The
fixture must not consume the owner's real key to simulate that independence.

For operator consultation, the primary documentation is the
[pass project documentation](https://www.passwordstore.org/) and the
[GnuPG manual](https://www.gnupg.org/documentation/manuals/gnupg/).
The requirements above remain unverified until the actual kit is prepared and
tested; these links are not evidence that any current replica or key is usable.

## Next real milestone

After this mechanism and its negative tests pass, reconcile the real source
baseline and implement the bounded capture tool under the
[operation contract](HEADSCALE-FIRST-REAL-BACKUP.md). Verify the recovery kit and
production producer/recovery scopes, consistency method, archive retention and
budget before requesting or executing the exact real operation tuple.

The first real acceptance record must distinguish the source capture, exact
external archive version, independent read/decryption and isolated native
recovery checks. Until then report this increment as **synthetic local
mechanism only**, with every remaining production or custody gap intact.
