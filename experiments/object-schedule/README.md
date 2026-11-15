# Restricted memory archive scheduling increment

Authorized on 2026-11-15: schedule the existing real memory-document backup,
use restricted credentials, and qualify independent failure/absence alerts.
No original deletion, source migration, paid service or VPS order is authorized
by this implementation. Initial source and destination remain the declared
corpus and the already initialized private seven-day Compliance archive.

`access-plan.json` declares two exact B2 data-plane identities, each scoped to
the one existing archive bucket and `memory-auto/v1/`, expiring after 90 days:

- Writer: `writeFiles` only; no read, delete, retention or account administration.
- Verifier: list/read and read-only encryption/retention inspection; no write,
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
