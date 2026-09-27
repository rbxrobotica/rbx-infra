# Satwake Commerce sandbox schema hold

Status: **draft only**. The [Commerce sandbox bootstrap runbook](https://github.com/rbxrobotica/rbx-commerce/blob/main/docs/runbooks/satwake-commerce-sandbox-bootstrap.md) is the source for the database checks. This change only sets `rbx-commerce-sandbox` API replicas to zero in Git so Argo self-heal cannot restart its writer during a separately approved sandbox backup and migration. Merging this PR triggers Argo auto-sync and temporarily removes sandbox checkout and callback handling. It does not alter production, the sink, Secrets, database schema, provider configuration or image pins.

## Entry gate

1. Obtain specific approval for the sandbox hold and database window. Confirm the effective database target is `rbx_commerce_sandbox`, current tracker/schema state, image and source Secret property names without printing credentials. On 2026-09-27 03:49 UTC, a read-only query as `postgres` found no `commerce` schema or `public.schema_migrations` and zero active sessions; the database was 7,567 kB on PostgreSQL 16.15. A second read-only query using the actual `rbx_commerce_sandbox` runtime role over a short-lived SSH tunnel confirmed the same missing schema/tracker. These observations are not a frozen state. The host had over 346 GB free on `/var/backups` at preflight, and no sandbox backup directory yet.
2. Confirm the Asaas sandbox webhook remains disabled and no checkout, callback, reconciliation, migration Job or other writer is running. A read-only Asaas API check at 2026-09-27 03:58 UTC found one webhook and confirmed it disabled by individual detail lookup; repeat this immediately before the hold. Record a plan to recover any inbound request that arrives while the API is paused.
3. Confirm the isolated `satwake-email-sink` remains healthy and that its NetworkPolicy is unchanged. This PR must render only the Commerce API replica change.

## Approved execution sequence

1. Merge this hold only after separate deployment approval. Wait for Argo `Synced/Healthy` with Commerce API zero pods and sink one healthy pod. Recheck database sessions and stop if any writer remains.
2. Take a restricted backup of the sandbox database, including any tracker, verify its checksum/listing and restore it into a separate temporary database. Do not infer a backup is valid from file existence.
3. Apply Commerce `000001`–`000017` using the sandbox runtime role, verify zero subscriptions and invites while writes remain stopped, then apply `000018`–`000023`. Require tracker `(23, false)`, owner/grant checks and the single cutover row. Stop and keep the API held on any deviation; never `migrate force` a dirty tracker.
4. Use a separately reviewed GitOps change to resume one API replica while preserving the inert `COMMS_API_URL=http://127.0.0.1:1`. Verify health, runtime database access, no provider callback side effect and Argo sync before any image or Secret mapping promotion.

The hold itself is not approval for step 2 or 3. The sandbox and production databases must remain isolated throughout.
