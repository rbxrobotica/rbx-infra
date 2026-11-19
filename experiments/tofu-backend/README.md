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

The next-candidate comparison now prefers native GCS (generation-guarded
locking and native history) with AWS S3 native locking as fallback. GitLab.com
is an option if suitable existing access is available; R2 is deferred on the
native historical-version gap. See the
[pending contract](next-candidate-contract.json) and the detailed comparison
in private resilience PR13. These are public-research/preparation results;
no alternative-provider account, fixture or successful live qualification has
been created. Actual account/billing, scoped identity and independent custody
must be verified before provider mutation. The browser runtime was unavailable
and no candidate credentials were found in local/ThinkCentre metadata checks.

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
