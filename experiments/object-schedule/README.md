# Restricted memory archive scheduling increment

Authorized on 2026-11-15: schedule the existing real memory-document backup,
use restricted credentials, and qualify independent failure/absence alerts.
No original deletion, source migration, paid service or VPS order is authorized
by this implementation. Initial source and destination remain the declared
corpus and the already initialized private seven-day Compliance archive.

`access-plan.json` declares two exact B2 data-plane identities, each scoped to
the one existing archive bucket and `memory-auto/v1/`, expiring after 90 days:

- Writer: `writeFiles` only; no read, delete, retention or account administration.
- Verifier: list/read and read-only file retention inspection; no write,
  delete, bypass or key administration.

The setup path may use the owner's administrative credential to issue these
custom capabilities through the Native API. It must not deploy that credential,
the Healthchecks management API or the owner's GPG private key to a worker.
Any existing master credential should be reused; generating a new master in
the console invalidates the old master, which requires owner participation.
The standard console access profiles do not provide individual capability
selection. Never substitute a standard profile silently if it admits extra
permissions. Preserve every existing key and archive.

Source admission requires read-only credentials for the declared document
corpus, including verified denial outside it. No fallback to the broad owner
S3 key is permitted in a scheduled runtime. Account-panel user roles are not
assumed equivalent to data-plane S3 bucket authorization. A source-policy change
requires the exact principal, current encrypted configuration backup, reviewed
merge/diff and denial/read probes before deployment.

Intended cadence is six-hour capture with one-hour external grace. A separate
read-only witness checks archive freshness, signature, complete encrypted
readback and retention every 15 minutes with 15-minute external grace.
Healthchecks hosted outside the executor detects missing signals if the host
or job stops. Separate processes on one host do not establish independent-host
compromise resistance. Full plaintext restores remain separate owner-controlled
drills; a green encrypted readback is not a new plaintext restoration.

The local systemd 255 host needs administrative installation for protected
system credentials; its tool does not support user credential encryption.
Prepare and review source, tests, custody, check/diff and exact activation
command before requesting owner authentication. Timer installation/activation
must never be claimed from a source file alone.

The historical permanent VPS candidate was for isolated IRC/RBXNet; temporary
compute was for clean provider-exit recovery. Moving a backup coordinator to
an always-on independent VPS is a separate sizing/operating decision. Keep the
external missed-heartbeat service outside the executor's failure domain and
re-evaluate shared underlying providers at supplier selection time.

## Implemented qualification and activation boundary

The producer uses conditional reads and the complete reconciliation from
`object-corpus/archive.py`, encrypts to a public-only owner keyring in temporary
storage, and signs a closed receipt with a dedicated Ed25519 workload identity.
Only ciphertext and the small signed receipt are uploaded. The receipt is
published last. The witness validates the pinned signature, capture age, full
ciphertext hash and actual Compliance protection on both uploaded files.
It cannot decrypt or upload, and its runtime contains no password-store or
owner management client. A rotation warning is signaled 14 days before the
90-day B2 key expiration while captures can still complete.

`recover.py` verifies the workload signature and trusted ciphertext/manifest
hashes before owner-side decryption, then pipes plaintext into the same offline,
non-root, read-only, disposable tmpfs recovery sandbox as the first manual
archive. Historical restores do not require a still-fresh backup timestamp.
Neither a producer upload nor an encrypted witness is counted as a new full
plaintext restoration.

`prepare.py` constructs a hashed public-only release from the existing official
dependency set and owner public key. The release contains its own dependency
copies; a scheduled worker must not depend on disposable build-cache paths.
`activate-local.py` reads only the exact scoped entries, refuses the known owner
S3 access key, and sends material to the root installer through stdin.
`activate-root.py` refuses existing install state, verifies the full release,
requires source/readback/restore admission, host-wraps each service credential,
runs Ansible check/diff/apply/check, and runs both real services successfully
before enabling either timer. It touches no existing DNS unit. A failed first
activation needs a reviewed update; blind reinstallation is refused.

The November 15 Contabo candidate **failed admission**. S3 credential
provisioning was delayed and eventually completed while the email remained
unverified; personal password setup was not demonstrated to be a prerequisite.
The custom non-admin role, despite `accessAllResources=false`, returned an S3
key that could list all ten existing buckets. Disabling the candidate and
removing its roles did not deny the immediate data-plane read probe. The owner
key was explicitly compared and excluded from every withdrawal operation.

The newly created, unused candidate user and its unassigned custom role were
withdrawn. Empty-body PATCH/DELETE requests returned HTTP 400; sending an explicit
empty JSON object allowed the control-plane withdrawal. Native S3 revocation is
a separate gate: do not infer it from user removal. The rejected key was never
printed or deployed; private encrypted custody preserves its audit history.
The later paired probe returned HTTP 401 for the retired key while the original
owner key still listed all 31 documents; revocation was thus verified separately,
within an approximately eight-minute checking window after removal. This is an
observed bound, not a provider revocation SLO.
No original key, user, object or source bucket policy was changed. The earlier
personal-invitation handoff was withdrawn. Its token had appeared in a diagnostic
view; the invitation target was subsequently removed. The owner explicitly
authorized the inquiry on November 15. The email attempt received an automated
unmonitored-mailbox notice, so the same approved text was submitted through the
authenticated support portal and an open ticket was confirmed. Private delivery
proof is in the companion evidence repository; no technical answer is claimed.
The inquiry text contains no credential or real resource identifier.

No qualified source-reader entry, protected worker, timer or continuous capture
is claimed. Require a supported, tested default-deny source arrangement and
documented revocation behavior before issuing/deploying a replacement. Admission also
requires any authentication handoff to be demonstrably closed; password setup
alone is not proof of token-replay behavior.

Live B2 qualification used only a new, clearly synthetic 44-byte fixture under
the exact owned prefix, outside the completed-archive naming pattern. Writer
list/delete, verifier upload/delete and verifier read outside the prefix were
denied; full readback and existing default Compliance retention passed. The
fixture remains protected; no original or prior backup was removed. A separate
60+60-second Healthchecks absence drill delivered DOWN and recovery UP to the
existing Gmail integration, then was paused. The real capture/witness checks
remain NEW until genuine executions qualify them.
