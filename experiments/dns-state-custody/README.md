# Source-side DNS state preservation

`state_archive.py` prepares owner-encrypted recovery copies without changing a
backend, source state, lock, provider resource or signing identity. It is a
remediation tool for the local-state custody gap, not an OpenTofu migration.

The owner authorized investigating the two ThinkCentre copies and preparing
their encrypted backup. Execute only against the four reviewed DNS state paths.
The helper rejects source locks, symlinks, changes during capture, unexpected
formats and non-private output directories. It compares lineage, serial and
managed bindings internally; only aggregate results and file metadata leave the
process. A supported canonical candidate still needs source/live-resource and
operator reconciliation; the helper never selects or activates a backend.

The archive contains all four original byte streams and a bounded manifest,
built in RAM and encrypted to a full verified owner GPG recipient fingerprint at
the source. Only a new mode-0600 `.tar.gpg` file in a mode-0700 directory is
written. Transfer ciphertext only. Preserve the manifest digest through a
separately controlled receipt. `verify` decrypts in RAM, permits only the exact
five regular archive members, and checks all four source hashes without
extracting files or overwriting live state. This is an in-memory restore test,
not a remote-backend test or a production provider rebuild.

Before capture, record the authorization, source paths/checksums, lock/process
observation, recipient agreement and destination. Missing global change-freeze
evidence remains explicit. Run capture on the source host, repeat source
metadata checks, transfer the ciphertext, then verify at the independent
destination. Do not count cloud or offline custody until that copy is actually
retrieved and decrypted. Do not print GPG stderr, raw state, manifest contents or
recipient identity. No new credential or cloud bucket is created by this code.

```sh
python3 -B -m unittest discover -s experiments/dns-state-custody -p 'test_*.py'
```

Execution receipts belong in private resilience evidence. Archives and state
remain outside Git. Follow `docs/runbooks/DNS-STATE-CUSTODY.md` before any later
state migration, plan or apply; archive immutability and an active backend's
exclusive lock require separate configurations and qualification.
