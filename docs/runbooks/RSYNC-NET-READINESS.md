# rsync.net requirements and budget

Status: procurement preparation, October 12, 2026. The operator confirmed that
no rsync.net account was available on that date. That preparation opened no
account, made no purchase, sent no vendor contact and created no credential.
This does not mean there was no earlier commercial correspondence. The PostgreSQL direction is recorded in
[ADR-0012](../adr/ADR-0012-pgbackrest-cluster-backup-jaguar.md); extending it to
Headscale requires a separate recovery design.

## Region reconciliation (2026-10-29)

The operator supplied a 2026-08-31 vendor response stating that Zurich had been
withdrawn for new contracts, with Denver or Silicon Valley offered instead.
The message had been read in the originating session; this reconciliation did
not reopen or export email. It establishes neither purchase nor acceptance of
a substitute region. The October locations-page claim below is not sufficient
to establish an offer available to RBX in the selected region.

Keep region and contracting unresolved until an authenticated, account-specific
commercial offer and owner decision reconcile the historical ADRs. No provider
or region is selected by this update. See [the evidence timeline](../infra/BACKUP-EVIDENCE-RECONCILIATION-2026-10-29.md).

## Historical price research from 2026-10-12

Official pages checked on October 12. USD amounts exclude applicable taxes and
payment/currency-conversion costs. No student, promotional or prepayment discount
is assumed. Recheck the selected offer before purchase.

| Required capacity scenario | Standard, minimum 800 GB | Technical rclone, minimum 200 GB |
| --- | --- | --- |
| 100 GB | 800 GB purchased: $12/month | 200 GB purchased: $24/year paid upfront |
| 500 GB | 800 GB purchased: $12/month | 500 GB purchased: $60/year paid upfront |
| 1,000 GB | $15/month | $120/year paid upfront |

Standard is $0.015/GB/month and includes seven daily snapshots and support.
The order page inspected on October 12 offered monthly, yearly and two-year
billing; no discount was verified in that research. Sources: [pricing](https://www.rsync.net/pricing.html) and
[order form](https://www.rsync.net/signup/order.html).

The technical rclone offer is $0.01/GB/month, paid annually. Despite its name,
it accepts tools over SSH/SFTP, not only rclone. It advertises snapshots but
excludes additional logins/subaccounts and product-specific integration support.
Different directories under the same login do not establish credential isolation.
Source: [rclone offer](https://www.rsync.net/products/rclone.html).

The October research inferred Zurich availability without a region surcharge
from the [locations page](https://www.rsync.net/products/locations.html). That
inference conflicts with the August commercial response and is withdrawn as
procurement evidence. Current region availability was not rechecked here.

The Standard plan is the proposed budgeting baseline for multiple RBX components,
subject to measured capacity and verification of subaccount restrictions. The
$24/year offer is an economical single-scope pilot candidate; do not select it
for combined Headscale and PostgreSQL merely because it is cheaper. Separate
write and recovery authority must be demonstrated. The provider documents
[subaccounts](https://www.rsync.net/resources/faq.html), but that FAQ's old price
figures are not used here. No purchase decision has been made.

Do not substitute the Borg offer assuming identical protection: its current
page excludes free filesystem snapshots. Additional snapshot retention can
consume quota. Sources: [Borg terms](https://www.rsync.net/products/borg.html)
and [snapshot behavior](https://rsync.net/resources/howto/snapshots.html).

## Requirements before contracting and activating

| Requirement | Concrete preparation | Acceptance evidence |
| --- | --- | --- |
| Ownership | Confirm offered region and scope against the owner-approved ADR; name billing/recovery owners | Account and escalation register, without secrets |
| Capacity | Measure consistent encrypted Headscale archive; separately measure PostgreSQL full, differential and WAL volumes through a retention cycle | Measured sizes and peak usage including rotation overlap and snapshots |
| Access separation | Distinct administrator, component uploader and restore authority; verify scope on each identity | Positive/negative access tests; revocation of one identity does not revoke all recovery |
| Encryption | Encrypt before upload; separate encryption custody from upload credentials | Restore using independently held recovery material |
| SSH authenticity | Verify assigned endpoint fingerprint against the provider's authenticated published reference; require strict host-key checking | Recorded fingerprint, provenance and successful SFTP validation |
| Retention and age | Define full/differential/WAL and snapshot retention; establish failure and age alerts | Backup identifier, success timestamp, monitored age and retention test |
| Recovery objectives | Record acceptable loss (RPO) and outage (RTO) per component | Timed isolated restore within the chosen objectives |
| Independent recovery | Keep encrypted password-store remote and offline recovery-key copy outside the protected host | Another authorized operator can follow the runbook after loss of that host |

PostgreSQL capacity cannot be inferred from Headscale archive size or Jaguar's
free disk. These price rows are scenarios, not a storage-sizing result. The
shared PostgreSQL cluster needs a scope decision covering its other databases;
backing up a cluster is different from backing up only an IRC mailbox.

The official pgBackRest guide documents SFTP repositories and client-side
repository encryption. That makes rsync.net a plausible destination, not a
completed compatibility test for RBX's configuration. SSH encrypts transport,
not the remote payload by itself. Sources:
[pgBackRest guide](https://pgbackrest.org/user-guide.html),
[server fingerprints](https://www.rsync.net/resources/fingerprints.txt) and
[SSH key setup](https://www.rsync.net/resources/howto/ssh_keys.html).

## Implementation sequence after the account decision

1. Prepare the IaC in two stages: key provisioning/registration, then repository
   activation. The current pgBackRest role mixes those steps and must not be run
   against a new account unchanged. Validate required account/path/fingerprint
   fields before package installation or secret retrieval.
2. Validate the SFTP endpoint and credential boundaries with disposable encrypted
   test data. Provision only public keys on the storage provider; keep private
   keys and passphrases in their approved custody.
3. Perform an isolated backup/restore rehearsal and retain checksums, source
   backup identifier, recovered point, elapsed time and integrity results.
4. Present the exact live change and rollback. PostgreSQL WAL activation may
   require a restart of the shared database service; the provider account alone
   does not authorize that restart or an IRC cutover.

Until these gates are met, local Headscale archive metadata and the existing
PostgreSQL preflight remain the evidence. Neither demonstrates external recovery
for the proposed design. See [Headscale reconciliation](HEADSCALE-RECONCILIATION.md)
and the [PostgreSQL live preflight](../../infra/irc/docs/POSTGRES-LIVE-PREFLIGHT-2026-10-12.md).
