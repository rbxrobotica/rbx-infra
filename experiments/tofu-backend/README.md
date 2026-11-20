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

## Executed disposition: November 19, 2026

**The tested B2 endpoint/configuration is rejected as an active backend.**
OpenTofu1.13.9 initialized two independent S3 backend directories, but the
synthetic apply and two concurrent native plans failed acquiring the lock.
The required conditional PUT closed the connection, including an independent
Content-MD5 probe. No native state version was written. This is an observation
about this pinned combination, not a claim that all B2 configurations or future
versions are unsupported. Never work around it with `-lock=false`.

The separate loopback HTTPS control passed nine gates, including contention,
wrong-owner refusal, ordinary release/reacquisition and read-only encrypted
artifact validation after shutting down the server. It is an in-memory test
double, never a deployed or selected production backend.

Nineteen passive-storage gates then passed for actual engine-encrypted
artifacts: distinct B2 versions, old-version retrieval, AES256 server-side
encryption, separate one-day Compliance copies, denied unrelated prefix/bucket
access and version deletion, ThinkCentre encrypted copy/readback, successful
read-only recovery of both generations, and rejection of wrong/missing keys,
corruption and untrusted manifests. Unavailable/denied backends stopped safely
without a root/emergency state file. These capabilities do not repair native
lock acquisition or qualify real-state migration.

Retained S3 uploads need an explicit integrity checksum. Failed attempts are
preserved separately; checksummed archive PUT succeeded while the conditional
mutable-bucket PUT still failed. The S3 checksum requirement is described in
the [official Object Lock documentation](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock-managing.html).
Two one-day, single-bucket probe identities were added after the archive
transport investigation. There are six lab identities total (four original,
two probes); mutable runtime credentials remain unchanged and never receive
administrative or version-deletion capabilities.

Source: [infra PR413](https://github.com/rbxrobotica/rbx-infra/pull/413), stacked
on draft PR394. Detailed sanitized receipts and the independent assessment are
in private [resilience PR13](https://github.com/rbxrobotica/rbx-resilience/pull/13),
stacked on draft PR11. No original state payload, plan, credential or encryption key
is committed. The original password-store worktree remains untouched; new lab
entries are in its separate encrypted feature branch.

## Primary-operated entry points

On 2026-11-20 the owner selected Infomaniak for the next synthetic qualification,
with Azure Standard Hot ZRS as the comparison. This changes the test order;
it does not overturn the Azure state-write fencing finding below. Infomaniak
login succeeded, but Public Cloud activation is blocked at outbound telephone
verification. The shop offered international ringing-call destinations and no
Brazilian destination. No product order, cloud project, bucket, key or charge
was completed. The owner-authorized support question was sent through the
official support center as ticket `INK-ETZ-65788-492`; a response is pending.

`infomaniak.py` is a separate guarded adapter for native Ceph endpoints in
`gva1`/`gva2`. Its backend configuration enables native lock files and SSE-C;
the engine also enforces its own AES-GCM state and plan encryption. The
adapter does not admit legacy Swift endpoints or reuse the B2 binding.
Infomaniak documents shared ownership for all identities in one tenant:
a dedicated synthetic project is required, and separate keys in that project
must not be described as independently restricted reader/writer identities.
Only offline guards and offline SDK SSE-C serialization have been exercised.
There is no completed Infomaniak setup/qualification/recovery runner or live
receipt yet. Do not invoke this adapter until the actual tenant, scoped
custody, setup diff and live admission have been reviewed and checkpointed.

The preceding role-specific research preferred Azure Standard Hot ZRS,
with GCS next and AWS S3 Standard as the native S3 reference.
The pinned Azure client attaches the lease ID to state writes; its infinite
lease, empty-blob initialization and orphan recovery require real tests.
GCS's generation-checked unlock does not fence state writes. This supersedes
the preliminary GCS-first cost-based order. Public benchmarks do not measure
the RBX state/lock workload from its actual client paths and cannot establish
a local latency winner. GitLab.com remains an access-dependent option and R2
is deferred on native history. See the
[pending contract](next-candidate-contract.json) and the detailed comparison
and role-specific research in private resilience PR13. These are research and
preparation results;
no alternative-provider fixture or successful live qualification has been
created. Actual account/billing, scoped identity and independent custody
must be verified before provider mutation. Prior browser failure was resolved
with a local workspace-path compatibility symlink; it is no longer the access
blocker. Detailed Infomaniak findings and sanitized receipts are in private
resilience PR13 under the 2026-11-20 experiment and evidence paths.
The initial qualification budget remains <=1MiB, 200 mutating/list operations
and 1,000 read operations. It provides only small-sample timings, not a
defensible p99; a larger performance study needs a separate reviewed budget.

The ClouDNS Premium M quarter is already separately contracted for secondary
DNS. It does not replace state locking or encrypted object recovery.

Run offline guards without network or credentials:

```sh
python3 -B -m unittest discover -s experiments/tofu-backend -p 'test_*.py'
```

The live entry points are `qualify.py setup|run`, `storage_controls.py
control|storage`, `probe_setup.py` and `checksum_probe.py`. Use a reviewed
preflight, pinned binary, existing verified SDK directory and a new evidence
receipt filename. Run engine invocations through `rbx-build-cache run --` for
the disposable tool cache lease; state, custody and evidence stay on primary.

The storage stage requires the exact successful control receipt and SHA256,
verifies it against the pushed `origin/codex/synthetic-tofu-backend-20261119`
receipt, and checks exact owned cipher paths/hashes before any upload. It does
not initialize or apply a local backend. Recovery uses `tofu show -json` on an
explicit encrypted artifact with cloud credentials absent; output stays in RAM
and only classifications/digests/expected-generation matches enter receipts.

Do not rerun or overwrite a completed attempt blindly. Existing identities,
object histories, local TLS material or the fixed ThinkCentre task directory
cause a stop requiring review. Failed attempts remain failures. The declared
payload budget is <=1MiB for all attempts; the executed closeout retained
9,979 payload bytes. No cleanup/lifecycle/paid-plan change was performed. Lab
identities expire after one or seven days; snapshot retention is a one-day
test policy, not a production retention choice.
