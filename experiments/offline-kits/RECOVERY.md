# Open and verify the complete owner recovery capsule

This is an owner-led recovery drill. It does not establish succession authority,
provider recovery-code validity or backup coverage of all company data. Required
independent factors: the outer kit phrase and the GPG-key passphrase, recorded
physically outside the vault and separated from the encrypted media.

## Clean recovery environment

Use a trusted Linux OS with GnuPG, Python 3, Git, bubblewrap and a functioning
systemd user manager with memory control. Verify tool provenance before the
incident; do not depend on a cloud login to retrieve the only opening procedure.
The capsule contains the reviewed recovery programs under `tools/`, in addition
to this procedure. Media-supplied executable code and SHA256SUMS alone are not an
independent trust anchor. Prefer the program hash/commit from the protected
generation record or a separately trusted repository checkout.

Keep the other known-good kit disconnected. Identify this physical unit from
the protected role mapping, mount it read-only, and compare its closed-file
digest with the protected generation record. Never format or repair the media
as part of a recovery attempt. Retain failing bytes/evidence.

## Opening without a preinstalled RBX program

The outer file is standard OpenPGP symmetric encryption. A trusted system
`gpg --decrypt` can open it without the lost private key. The inner archive
contains a separately passphrase-protected GPG private-key backup. The outer
phrase is not sufficient to decrypt vault entries by itself.

Perform any opening in a bounded no-swap process scope with core dumps disabled.
Use a fresh private GPG homedir on transient memory, a hidden operational home,
a new agent and no network interfaces except loopback. tmpfs alone does not
prove that memory cannot be swapped. Native pinentry is the only input route
for real factors. Do not put phrases in shell history, argv, environment,
clipboard, screenshots, transcripts or log files.

If the reviewed RBX program is unavailable, authenticate the entire outer file
using trusted GnuPG into private, no-swap transient memory. Require successful
GPG authentication and exit status **before** inspecting or extracting the tar.
Do not pipe a still-unverified decryption stream directly into extraction.
List and inspect archive members first: regular files only, no links, devices,
absolute or parent-traversing paths, duplicates or unbounded sizes. The private
manifest maps each file's length and SHA256. The authenticated `tools/` members
provide the scoped verifier; do not execute an unauthenticated media program
just to obtain that verification.

The controlled implementation reads the raw tar in memory; it never persists
an unwrapped key-bearing archive. An operator adapting a bootstrap must retain
that property. If those isolation requirements cannot be met, stop and prepare
the clean recovery host before opening the capsule.

## Full verification

1. Run `restore_sandbox.py --check` with the exact reviewed program and ciphertext
   hashes before a real unlock. This launcher currently permits the qualified
   ThinkCentre host only. A replacement host needs its identity guard reviewed;
   do not remove isolation gates merely to bypass that guard.
2. Explicitly launch the real restore with the same reviewed arguments. The
   launcher masks the operational home, `/run` GPG sockets and keyrings, disables
   external networking, bounds memory and disables swap/core dumps. Only native
   desktop UI sockets required for pinentry are exposed.
3. Read the outer phrase from its independent physical record. Import the
   retained protected private key into the fresh agent and supply its separate
   passphrase through native pinentry. No operational `pass` or cached owner
   keyring is used for this drill.
4. Require authenticated canary decryption and its expected length/hash, then
   authenticated opening of **every** current `.gpg` entry and every retained
   historical ciphertext blob. The `current/` overlay includes encrypted
   uncommitted entries and recipient maps. Other untracked files were excluded
   without deletion. No decrypted credential is printed or persisted.
5. Rebuild the encrypted-entry Git repository from `password-store.bundle`;
   require `git fsck --full`, exact main head, retained commit count and exact
   reconciliation of the historical ciphertext set. A canary/hash-only result
   does not qualify complete vault recovery.
6. Record only neutral kit role, exact closed generation/hash, source commit,
   counts, timing, interventions, failure and cleanup. Kill the temporary agent
   and remove the transient keyring. Do not convert a failed attempt to a pass.

## After the drill

Safely unmount, disconnect and return the validated unit to its protected
custody. Test the other unit separately. Local physical readback followed by
LAN transfer of the exact closed bytes is evidence for that byte chain; it is
not direct ThinkCentre USB handling or a power-cycle/long-retention test.

Refresh after material recovery changes and at the 60-day owner review. Add a
fresh closed generation; preserve older required keys and packages until
retirement is explicitly reviewed. Never expose both good offline copies to
the same update before one has been read back and restored successfully.
