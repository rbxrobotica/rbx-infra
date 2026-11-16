# FlightDeck private chat intake pilot

Owner: Leandro, Merovelis. The owning application is rbx-flightdeck PR 25.
Capture precedes Workstream routing. It is never verified execution, an Action,
a capacity movement, a lead qualification or external communication.

## Rollout gates and source

The PostgreSQL recovery point and cluster backup are documented in
`FLIGHTDECK-BACKUP.md`. Application auto-sync is held by PR 404. Verify that hold
and an idle operation before credential materialization or selective rollout.
Do not rely on the previously broken optional integration as a hold.

On 2026-11-16 the core ExternalSecret requested three absent optional Public
Presence fields. The old pods remained available while sync was blocked in
wave 0. This pilot keeps that unconfigured trigger suspended and makes those
runtime references optional. It does not provision or activate Public Presence,
Maestro, publication or person-addressed communication. Their later activation
needs its own scoped verified setup. If those credentials have appeared since
preflight, stop and coordinate instead of disabling an active integration.

The intake uses two distinct random purpose credentials held under:

- `rbx/flightdeck/chat-capture-token`: owner-host capture only. Only its SHA-256
  enters Kubernetes/PostgreSQL.
- `rbx/flightdeck/review-worker-token`: Sunday preparation and a read-only export
  of the explicitly configured pilot owner's private weekly inbox. It cannot
  approve dispositions, route evidence or execute actions.

Generate new values directly into pass, preserve its existing private encrypted
remote, and never print them. The narrow Ansible playbook
`bootstrap/ansible/flightdeck-intake-credentials.yml` checks the exact cluster,
application hold and existing named Secret. Check mode reads metadata and plans
only. Apply adds or verifies the two credential fields and private source reference through the controlled secret flow,
preserving all other fields. A mismatch requires coordinated rotation; it is
never overwritten automatically. Do not invoke the fleet-wide secrets role.

The admitted chat ID is kept in `rbx/flightdeck/chat-source-thread-id`, not a
public GitOps value. The source Secret carries that private reference for the
bootstrap. It is not an authentication credential.


## Deployment order

1. Confirm the backup image and database recovery proof. Reconcile source S3
   read RBAC before only backup resources. Check receipt, exact object version
   and independent download. Preserve encrypted owner-host custody.
2. Verify app PR CI and independent review; publish and scan only its main image.
   Keep automatic app sync held while its image pin is promoted.
3. Materialize the two scoped intake fields from pass through the narrow
   playbook using a temporary 0600 kubeconfig. Remove the temporary file after.
4. Reconcile the app ExternalSecret and suspended unconfigured trigger. Check
   Ready without dumping Secret contents. Do not prune.
5. Sync migration hooks, existing operator bootstrap, intake bootstrap, then the
   Deployment and review resources at the exact reviewed image revision. The
   connector bootstrap binds one existing owner Workspace and one allowed chat,
   records the service identity honestly and never re-enables a revoked grant.
6. Install `scripts/intake/install.py` from the reviewed FlightDeck source with
   an explicit chat ID and `--since-utc`. It reads documented native pagination
   without changing Codex configuration, starting/resuming chats or calling a
   model. Only text user messages and completed final replies are admitted.
7. Check a real queue-to-API receipt and exact replay, then a timer-created
   collector execution. A fixture or direct POST is not source/scheduler proof.

## Sunday review

The cluster CronJob runs at 18:00 America/Sao_Paulo and prepares an append-only
snapshot, independent of the desktop. The immutable record IDs, hashes and
review dispositions use stored capture/audit timestamps through the cutoff.
Preparation retries cannot rewrite an earlier snapshot. Older NEW/KEPT records
carry forward until reviewed; later arrivals do not disappear between weeks.
The owner reviews at `https://flightdeck.rbxsystems.ch/intake` and explicitly
keeps, dismisses or routes relevant evidence into an accessible Workstream.

A read-only Sunday observer can use the separate review credential on
`GET /internal/intake/weekly-review?weekOf=YYYY-MM-DD&page=N`. The server binds
ownership; caller-selected owners or Workspaces are rejected. Pages contain up
to 50 current-week and 50 older pending records. Continue until both counts are
covered. The prepared snapshot metadata describes the saved cutoff; the inbox
entries represent their current review state. Do not present a later current
state as a state known at the saved cutoff. Preserve private input and report
artifacts encrypted, with source IDs and receipt hashes, without writing business
Signals or Decisions automatically.

The existing Plausible Sunday analytics review remains separate and active.
Source/transport failure, withheld item time, offline host, missing snapshot or
unobserved schedule means unknown coverage, not no work. Check source-status,
transport-status, pending queue and receipt files in the private owner state.
Stop the local timer to pause collection; revoke the grant through a coordinated
operator procedure if its credential is compromised. Preserve queued evidence
and exact keys; do not mint replacement keys after uncertain writes.
