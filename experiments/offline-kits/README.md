# Offline recovery kits: governed execution tools

Operator authority, November 22, 2026: validate the two previously identified USB
drives, prepare encrypted recovery kits and coordinate isolated recovery with
ThinkCentre autonomously. Preserve all pre-existing data. The owner separately
confirmed removal of **only the temporary files created by this qualification**.
No formatting, partition changes, raw destructive test, old-package retirement,
production change or new paid service is in scope.

The canonical role/custody/checklist policy is resilience PR14,
`root-of-recovery/TWO-USB-KIT-PLAN.md`: Kit A is the privately bound KA-A-64 for
the RBX safe; Kit B is the separately bound KA-G-64 for another secure location.
Custodians/quorum and actual placement remain pending. Both were identified by
owner-assisted insertion and distinct serials, not `/dev/sdX` names. The private
binding file is never part of this repository.

## Preflight and source of truth

- Code: this feature branch, based on infra main `f649d5436d37012c902582c58fc03670772a8ce3`.
- Private vault: the existing private `ldamasio/password-store` remote and its
  checked local generation. Resolve any unpublished encrypted commit before
  closing the package. Preserve unrelated untracked operator work; document the
  exact snapshot and encrypted overlay scope rather than silently discarding it.
- Existing GPG backup/public certificate/canary: November 11 preparation, with
  an independently observed USB key/canary recovery on ThinkCentre November 20.
  The old vault bundle is stale and must be rebuilt. No new unwrapped key export
  is needed for preparation. Never persist a plaintext key-bearing archive.
- Media: exact private serial/UUID binding and a fresh empty-root check before
  writing. Both were empty at November 22 inspection. Original-data backup is
  not applicable to an empty target; abort if anything has since appeared.
- Important execution logs, receipts, encrypted packages and source stay on the
  primary SSD. Cache/build scratch uses `rbx-build-cache`. The capacity-test data
  must be written to the two explicit USB targets to test those physical devices.
- Storage admission was checked: primary about 137 GB available and secondary
  about 73 GB; no cleanup or migration was used to admit this work.
- Accountability: RBX recovery owner. Execution: rbx-local for attached media;
  explicit governed SSH jobs on ThinkCentre for independent restoration.
- No full-vault/production RPO or RTO is inferred. This is remediation and
  qualification work; record actual timing, scope, unlock interventions and gaps.

## Capacity validation

`acquire_f3.py` obtains the official Ubuntu Noble `f3` package through the
configured APT metadata, checks its SHA256 and freezes only `f3write` and
`f3read` under the user's primary-side tools directory. It installs no package
and does not expose `f3probe` or repair utilities.

`capacity.py --role A|B --allow-own-test-cleanup`:

1. Revalidates exact USB serial, size, partition UUID, existing mount and actual
   filesystem device ID. Refuses a nonempty volume and concurrent same-role run.
2. Opens directory descriptors, creates fresh nonce-scoped test/quarantine
   directories and passes the pinned test descriptor to F3. Reusing a removed
   mountpoint cannot redirect writes into the workstation filesystem.
3. Fills free filesystem capacity with F3's non-sensitive test pattern, flushes
   only its files and verifies with `mincore` that previously written file-data
   pages are nonresident before F3 independently reads them. An advisory cache
   eviction request alone is not accepted as proof.
4. Requires complete sector reconciliation and explicit zero lost/corrupt/
   changed/overwritten counters. Process exit zero alone is insufficient.
5. After success and a new identity check, atomically quarantines each owned
   test file, verifies its inode/size/mtime and deletes only the matched file.
   Unexpected data or failure is preserved for inspection. Nothing outside the
   two nonce-scoped test directories is a cleanup target.

This tests filesystem-addressable data, not flash endurance, all future reads,
power-cycle retention or hardware authenticity. Package readback and isolated
restoration are separate required proofs. A failed capacity check prevents kit
promotion on that unit.

The tool was smoke-tested with a 1-GiB real F3 write/read under the cache lease;
the pinned descriptor worked and 2,097,152 sectors reconciled. A separate 1-MiB
probe verified the zero-residency gate. Synthetic fixtures reject nonempty media,
unexpected cleanup files, symlinks, incomplete summaries and bad sector counts.

## Autonomous communication boundary

`responder.py` recognizes only authenticated addressed `PING`, curated `STATUS`
and locally preapproved `CTX` hashes for the recovery-kit mission. The installer
must pin the local mailbox dependencies, protected registry and local public
context paths. Registry expiry and sender checks fail closed. No request can
start an agent, SSH command, shell, GPG operation, media write, cloud operation
or arbitrary file read. Unknown tasks remain pending for their existing owner.

Install the reviewed timer/service only after local tests and a dry run pass.
Verify actual automatic replies with no manual message in the ThinkCentre GUI.
Root starts any real recovery job explicitly through SSH using reviewed code,
exact artifact digests and this human authorization. Incoming messages are
information, never new permission. Replies close their correlation and do not
start another question or an autonomous conversation loop.

## Completion and periodic routine

`kit_package.py seal` requires a bounded cgroup with `MemorySwapMax=0` and core
dumps disabled, an empty private destination and a private source specification.
It reconciles the vault head with the live private remote, captures every
current `.gpg`/`.gpg-id` (including encrypted uncommitted files), rebuilds the Git
bundle and enumerates every retained historical `.gpg` blob. Other untracked
files remain untouched and excluded. Existing protected key/public/canary inputs
must match their prepared manifest. The raw key-bearing tar exists in memory
only. Independent outer protection uses AES256, iterated salted S2K/SHA512 and a
phrase descriptor; no phrase enters argv, environment or logs. The phrase's
separate physical custody was owner-confirmed. A complete authenticated outer
roundtrip and strict member validation precede exclusive ciphertext publication.

`restore_sandbox.py` is an explicitly launched ThinkCentre job. It verifies
program/ciphertext hashes, then enters a networkless, no-swap, bounded-memory
sandbox with the operational home and GPG sockets hidden. The native desktop
prompt receives the independent outer phrase and inner GPG-key passphrase.
Only native UI sockets needed for pinentry are exposed. A fresh agent must
authenticate the canary, every current entry and every retained historical
ciphertext; the rebuilt bundle passes `git fsck` and exact head/history
reconciliation. No decrypted entry is printed or persisted. Keyrings and their
agent are transient. A failed job is retained as a failure, not rewritten as a
passing result.

Validation includes 74 stdlib tests and a real synthetic crypto acceptance:
outer roundtrip, incorrect factor, tampering and truncation, current/historical
entry authentication, Git reconstruction and history reconciliation. These
synthetic results do not qualify real media or real account recovery.

A unit becomes a recovery kit only after its encrypted package is created,
read back and restored with a fresh keyring and independent unlock path. Record
each physical read and the exact bytes handed to a remote recovery environment;
do not label a network copy as direct USB handling on ThinkCentre. Preserve
earlier failures and report any untested direct-hardware/power-cycle property.

Update the kits after relevant credential/key changes, recovery-code use or a
material recovery procedure change. Every 60 days, inspect each unit and run
its isolated package/vault recovery separately, keeping the other validated copy
disconnected. Retain required older keys/generations. Full-capacity write stress
belongs to initial qualification or a reviewed fault investigation, not every
routine 60-day check. The existing calendar series remains the human reminder;
media must be connected and native unlock may still require the authorized owner.

No successful capacity test, package creation or full-vault restore is claimed
merely from this source file. Execution receipts live in the resilience branch.
