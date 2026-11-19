# PostgreSQL 16 synthetic recovery rehearsal

Operator and cleanup owner: Leandro Damasio. Runtime host: ThinkCentre
(`psyctl-ThinkCentre-E73`). Custody peer: `psyctl-workstation` via the existing
`rbx-local` SSH alias. Parent checkpoints: rbx-infra PR 394 at
`ae985ed7a43b535db1fd1ee8015dc82baa1cdadc` and rbx-resilience PR 11 at
`9d50e81900b14858d41b61f09b29021cab2fa1ab`.

| State | Source, custody, and recovery |
| --- | --- |
| Fixture and runbook | Committed SQL and harness on the stacked GitHub infra branch. The database is disposable and fully regenerable, with no prior capture or restore applicable to this new run. |
| Source and target | Distinct labeled rootless containers with separate bounded tmpfs PGDATA and Unix sockets, no published port or workload network. At most one active at a time. No production endpoints or service dependencies. |
| Closed synthetic backup | Nonce-specific child of `~/.local/share/rbx-agent-handoffs/pg16n19/` on rbx-local. Encrypted archive and digest receipt are retrieved over host-key-verified SSH. Trust anchor is the locally computed receipt SHA-256, independent of SSH retrieval. One generation; no storage versioning or periodic job. Each one-shot run records checks and timestamps. |
| Lab recovery material | Newly generated high-entropy lab-only passphrase in a separate 0700 task directory on ThinkCentre, never with the remote archive. Uses a separate GnuPG home. Loss of both key and plaintext does not lose any canonical data. Recreate the synthetic fixture from Git. |
| Evidence and retention | Sanitized evidence is committed on the stacked GitHub resilience branch. Leandro reviews and removes the nonce-scoped remote archive, receipt and local key after review, with a manual seven-day retention window from capture. Preserve evidence in Git. |

Preflight on 2026-11-19: both LAN identities verified. Rootless Podman reports
cgroup v2 with CPU, memory and PID controllers. The host has no alternate cache
disk or installed RBX cache wrapper; its disk guard reports warning at 89%
usage with about 12 GiB free. Use the already cached digest, bounded RAM-backed
server data and small, nonce-scoped working files. No image pull is required.
The PostgreSQL 16.15 amd64 image is the Docker Official Image
`docker.io/library/postgres@sha256:075f7ba66bc9b3ce7d6b8b635208ff61cd7cf1a67d71ec530eec5d7ae0cbe571`.
The registry manifest bytes matched that digest and declared config ID
`sha256:75f5a96988cdf694a215073c3e9c001b706b371e2f94df3967f2efdec2787f6b`,
matching local storage, and source revision
`docker-library/postgres@9d15534160ade17f2b6c455a39ee967c49b1937d:16/alpine3.24`.
Runtime server and client versions are checked separately.

The fixture is one UTF-8 database with a NOLOGIN owner and a restricted login
reader. It uses an explicit schema, privileges, Unicode strings, exact decimal
amounts, UTC-normalized timestamps, a foreign key, a check constraint, indexes,
binary payloads and hstore from the pinned image. Fixture writes end before
capture and there are no concurrent writers. Database objects are captured in a
custom-format logical dump; the committed, password-free role definitions are
a separately verified archive member. There is no multi-database snapshot, WAL
archiving, PITR, production backup, provider-independent custody, or production
key-recovery qualification in this experiment.

RPO observation: source fixture transaction commit to closure of a verified
off-host copy, with the restored state reconciled to that cutoff. RTO observation:
start of independent retrieval through the first successful reader query in the
fresh target, including verification/decryption, provisioning and restore.
Phase durations use the ThinkCentre monotonic clock. UTC timestamps use its wall
clock. Git and registry preparation, capture and upload, lab-key generation and
subsequent negative tests are reported separately from recovery elapsed time.
There are no production RPO/RTO objectives for this fixture.

Execution and review instructions accompany the harness. Run the offline checks
and use its dry-run before creating lab resources. On failure, preserve the
sanitized failure separately and check for owned residuals before a fresh nonce
is used. The parent production records and derived grades are outside this drill.
