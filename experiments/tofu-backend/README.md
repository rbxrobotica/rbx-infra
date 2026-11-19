# Synthetic OpenTofu remote-backend qualification

Authorized November 19, 2026: qualify a remote backend using synthetic state,
exclusive locking, history, encryption, recovery and fail-closed behavior.
No production state, migration, deployment, existing bucket/key/policy change,
service installation or paid plan is in scope. Do not use a local-backend apply
or `-lock=false`. Parent context: infra PR394 and private resilience PR11.

The first candidate is B2 with pinned OpenTofu1.13.9 and native `use_lockfile`.
The provider must actually enforce conditional PUT; S3 compatibility and a
successful ordinary PUT are not sufficient. A failed gate is retained as a
qualification failure, not bypassed. This lab does not qualify real-state
custody/migration, offline founder recovery or production RPO/RTO.

## Declared fixture preflight

| Field | Boundary |
|---|---|
| State | New `terraform_data` synthetic resources only; no cloud provider plugin |
| Canonical source | This feature branch, committed/pushed before live setup |
| Owner | Leandro; primary agent executes setup, secret custody and real probes |
| Remote | Two uniquely named new B2 fixture buckets; existing buckets untouched |
| Mutable backend | Private SSE-B2 bucket, keep-all history, no default retention on locks |
| Recovery archive | Separate new SSE-B2 bucket with one-day Compliance default; runtime cannot access it |
| Independent copy | Encrypted fixture snapshots under a separate task-owned ThinkCentre handoff path |
| Key custody | Lab-only encryption passphrase and scoped identities in separate encrypted `pass` entries; private Git remote checked |
| Administrative access | Reuse existing B2 setup credential only in the setup/closing process; never pass it to OpenTofu |
| Restore | None before first lab run; independent encrypted historical recovery is the acceptance experiment |
| RPO/RTO | Synthetic cutoff/age and retrieval/decrypt/functional-read timings; no production objective claimed |
| Retention/budget | Preserve tiny synthetic artifacts until review; <=1MiB uploaded payload budget, seven-day lab identities; no source deletion |
| Local risk | Sources/evidence/cipher snapshots on primary; binary download/extraction only on leased disposable cache |

Resources and credentials must be bound to the exact lab identifier before use.
No broad setup key is a fallback for a scoped runtime failure. No unknown lock
may be removed. Runtime identities lack version-deletion and account/key/bucket
administration. Negative destructive probes use only separate, owned synthetic
canaries, never a state snapshot or existing archive.

## Qualification evidence

Record tool/release hashes, exact endpoints/configuration, conditional operation
outcomes, two real engine lock contenders, normal release/reacquisition and
wrong-ID refusal, encrypted generations/history, independent retrieval,
wrong/missing key and corruption failures, inaccessible backend behavior and
least-privilege denial results. Never log raw state, environment, credentials,
signed URLs, unfiltered provider responses or state encryption material.

State and lock lifecycle are distinct. Historical immutability must not strand
mutable locks. OpenTofu's lock owner comparison and ordinary release are tested;
no automatic production unlock or alternate-writer promotion is implemented.

Official sources:
- https://opentofu.org/docs/language/settings/backends/s3/
- https://opentofu.org/docs/language/state/encryption/
- https://github.com/opentofu/opentofu/releases/tag/v1.13.9
- https://www.backblaze.com/apidocs/s3-put-object
- https://www.backblaze.com/apidocs/s3-delete-object
