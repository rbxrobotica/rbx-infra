# Bounded real document archive

This operator-run increment captures one complete, owner-declared corpus of
independent Markdown documents. It is not a database snapshot, bucket mirror,
historical-version migration or continuous backup service. Source credentials
are used only by the bounded read adapter. Detailed mappings, keys and payloads
are excluded from Git; the source plan is owner-encrypted outside Git.

Capture uses conditional S3 reads, two complete reconciled listings and hashes
of every actual payload. It records headers, tags and observed configuration
without applying source permissions at a destination. Cross-document atomicity
is not claimed. Version-enabled sources are refused pending a separately scoped
history design. Concurrent changes fail the capture rather than hiding a gap.

The archive has a fixed manifest and numbered blob members. GnuPG signs and
encrypts it to the pinned owner identity; a local decrypt/signature comparison
must pass before the ciphertext is accepted. Only ciphertext is durable locally.
The sanitized manifest and ciphertext hashes are checkpointed independently in
the private evidence repository before cloud upload.

## Destination configuration and first execution

The current owner explicitly authorized a first real Contabo-to-B2 backup with
isolated restoration and no original deletion. Desired new archive destination:
private bucket, SSE-B2, Keep all versions, Object Lock enabled and seven-day
Compliance default for this bounded first capture. Seven days is a minimum
protection window for the pilot, not an expiry/delete instruction or an adopted
ninety-day history policy. No account-wide lifecycle, canary policy, billing,
replication or public access changes are permitted. Record exact resource
identifiers only in the encrypted private custody record.

The console upload/download path can exercise the first capture without
creating a permanent application key. No existing broad account token is
extracted from the browser. A later recurring controller requires separately
reviewed scoped producer/recovery identities, freshness alerts and scheduling.
Do not claim such automation from a one-time upload.

The operator retrieves the new ciphertext independently from B2 and compares
its hash with the pre-upload trusted receipt. A fresh read-only, non-root Podman
sandbox has no network, source/provider credentials, owner keyring or host data
mount. GPG verification/decryption happens on the owner host and only the signed
plaintext bundle crosses a pipe into the sandbox. The recovered documents and
metadata sidecar exist only in disposable tmpfs. All file hashes, UTF-8 decoding
and CommonMark reads must pass. Run two separate fresh recoveries. This qualifies
document restoration from that capture, not a live agent service or S3 failover.

The first drill uses an already installed official Python container pinned by
local image digest and the already installed MarkdownIt/mdurl reader modules
mounted read-only. It neither installs software nor contracts restore compute.
Core capture uses the existing official botocore dependency set. Record exact
versions/hashes in the private execution receipt. No real payload is printed.

```sh
python3 -B -m unittest discover -s experiments/object-corpus -p 'test_*.py'
python3 -B experiments/object-corpus/archive.py capture \
  --plan /absolute/private/source-plan.json.gpg \
  --output /absolute/private/new-capture.tar.gpg
python3 -B experiments/object-corpus/archive.py recover \
  --archive /absolute/private/independently-downloaded.tar.gpg \
  --cipher-sha '<trusted-ciphertext-sha256>' \
  --manifest-sha '<trusted-manifest-sha256>' \
  --signer '<pinned-owner-primary-fingerprint>' \
  --image 'sha256:<verified-official-local-python-image>' \
  --markdown-package /absolute/public/package/markdown_it \
  --mdurl-package /absolute/public/package/mdurl
```

The initial corpus target is Tier-1 recovery within the existing 24-hour RPO/RTO
ceilings; classification/producer mapping still requires its governed component
record before production scheduling. A one-time capture cannot prove ongoing
RPO compliance, full-service RTO, independence from B2 itself, offline owner-key
custody or successor access. Keep these gates explicit. No original, prior
backup or cloud object is deleted by this implementation.
