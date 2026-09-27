# Satwake Commerce sandbox schema hold

Status: **hold executed on 2026-09-27**. This document preserves the entry
gates and sequence of [Infra #327](https://github.com/rbxrobotica/rbx-infra/pull/327),
not a pending deployment instruction. The [Commerce sandbox bootstrap runbook](https://github.com/rbxrobotica/rbx-commerce/blob/main/docs/runbooks/satwake-commerce-sandbox-bootstrap.md)
records the backup, migration and later release checks. The hold changed only
`rbx-commerce-sandbox` API replicas to zero in Git, so Argo self-heal could
not restart a writer during the approved sandbox backup and migration. It did
not alter production or the isolated sink.

## Historical entry gate

1. Obtain specific approval for the sandbox hold and database window. Confirm the effective database target is `rbx_commerce_sandbox`, current tracker/schema state, image and source Secret property names without printing credentials. On 2026-09-27 03:49 UTC, a read-only query as `postgres` found no `commerce` schema or `public.schema_migrations` and zero active sessions; the database was 7,567 kB on PostgreSQL 16.15. A second read-only query using the actual `rbx_commerce_sandbox` runtime role over a short-lived SSH tunnel confirmed the same missing schema/tracker. These observations are not a frozen state. The host had over 346 GB free on `/var/backups` at preflight, and no sandbox backup directory yet.
2. Confirm the Asaas sandbox webhook remains disabled and no checkout, callback, reconciliation, migration Job or other writer is running. A read-only Asaas API check at 2026-09-27 03:58 UTC found one webhook and confirmed it disabled by individual detail lookup; repeat this immediately before the hold. Record a plan to recover any inbound request that arrives while the API is paused.
3. Confirm the isolated `satwake-email-sink` remains healthy and that its NetworkPolicy is unchanged. This PR must render only the Commerce API replica change.

## Historical approved sequence

1. Merge this hold only after separate deployment approval. Wait for Argo `Synced/Healthy` with Commerce API zero pods and sink one healthy pod. Recheck database sessions and stop if any writer remains.
2. Take a restricted backup of the sandbox database, including any tracker, verify its checksum/listing and restore it into a separate temporary database. Do not infer a backup is valid from file existence.
3. Apply Commerce `000001`–`000017` using the sandbox runtime role, verify zero subscriptions and invites while writes remain stopped, then apply `000018`–`000023`. Require tracker `(23, false)`, owner/grant checks and the single cutover row. Stop and keep the API held on any deviation; never `migrate force` a dirty tracker.
4. Prepare the Secret and image under the hold, then use a separately reviewed GitOps change to resume one API replica while preserving the inert `COMMS_API_URL=http://127.0.0.1:1`. Verify health, runtime database access, no provider callback side effect and Argo sync before checkout or e-mail testing.

The owner separately approved the hold, restricted backup/restore and
migrations in the 04:15–07:00 UTC window. Infra #327 merged at `7d1016c`;
Argo reached `Synced/Healthy` with API 0/0, sink 1/1 and no API pods or Jobs.
The full sandbox backup was restore-tested. Migrations `000001`–`000017`
passed the zero-subscription/invitation gate; `000018`–`000023` completed as
the runtime role, with tracker `(23, false)`, owners and a single cutover row
verified. Production remained untouched.

Under a later specific authorization, [Infra #282](https://github.com/rbxrobotica/rbx-infra/pull/282)
merged at `1fdc55b` and mapped the approved sandbox Secret properties and
isolated OIDC project. [Infra #286](https://github.com/rbxrobotica/rbx-infra/pull/286)
merged at `849fba7` and pinned the new Commerce image. After each sync Argo
was `Synced/Healthy`; the API remained **0/0 without pods**, the sink stayed
1/1 and the Comms URL remained literal loopback.

[Infra #328](https://github.com/rbxrobotica/rbx-infra/pull/328) is a separate
draft proposal to resume the sandbox API at one replica. It was not merged at
this checkpoint, and approval for #327/#282/#286 does not authorize it. The
existing sandbox Ingress would expose the API if #328 were merged. Before any
release, recheck the database, Secret, image, disabled Asaas sandbox webhook,
hold, and the exact approval scope. No checkout, callback or message was
executed as part of the hold or the configuration releases.
