# Read-only object-storage inventory

This bounded collector enumerates credential-visible Contabo EU buckets,
current keys, every readable object version/delete marker, unfinished multipart
upload references and storage protection metadata. It does not fetch payloads,
delete objects, abort uploads, change bucket settings or create credentials.
Only an explicit command allowlist can reach the SDK transport. Existing
credentials come from `pass` in memory, never CLI arguments or written files.

The current execution uses the already installed AWS SDK v3.1073.0 in the site
workspace, Node 24.22.1, three SDK attempts, 5-second connect/20-second request
timeouts, 80ms between logical calls, 1,000 entries per page, 10,000 logical
requests, 5,000 aggregate prefix groups per bucket and a 100MiB raw manifest
bound. Request counts are reported. No new dependency is installed by the
collector. The operational command requires the corresponding installed SDK
and a previously verified full owner GPG recipient reference.

Full names, keys, policies and version metadata stream directly into GPG. Only
ciphertext is persisted, in a new owner-only directory; files are never
overwritten. Public/private-repo receipts use anonymous bucket/group references
and aggregate counts. The encrypted manifest must remain outside Git. Its
source hash, SDK version and capture boundaries are included internally.

Two paginated listings are not an atomic namespace snapshot. Current/latest
version count and byte agreement is reported as a consistency check, not proof
of application consistency or complete source payload capture. Missing access,
repeated/missing continuation markers and bounds leave coverage incomplete.
Nothing infers another region/account from a successful EU enumeration. An
absent bucket SSE configuration is not proof of unencrypted provider disks;
public-policy status is not proof that every object is anonymously readable.

This inventory is not a data backup or payload integrity/restore test. A single
observation cannot measure net growth. Prefix dates and upload timestamps do
not prove that originals are disposable. In particular, a Restic repository
requires snapshot-aware retention and must not receive a naive age-based
bucket lifecycle that can remove chunks referenced by retained snapshots.

```sh
node --test experiments/object-inventory/test_inventory.mjs
node experiments/object-inventory/inventory.mjs /absolute/private/new-directory
```

The owner authorized this inventory and per-corpus policy/cost proposals on
2026-11-15, explicitly prohibiting deletion. Journal publication is separately
authorized for new public content only; it must not disclose this inventory.
