# Satwake Commerce schema maintenance window

Status: staged only. Merging this change triggers Argo auto-sync and takes the production Commerce API and web UI offline. Obtain explicit approval for the exact maintenance window and production migration before merging it.

## Why this GitOps change is needed

The live `rbx-commerce` Argo Application and its `root` parent both have automated self-heal enabled. An ad hoc `kubectl scale` or CronJob patch can be reverted. This proposal sets the API and web Deployments to zero replicas and suspends `rbx-commerce-renewals` in Git, so the pause persists while the approved schema work runs. It does not apply a migration, change a Secret or promote an image.

The Commerce API is the writer for public checkout, provider callbacks, reconciliation and seat assignment. The web UI cannot complete its API flows while the API is paused. The renewal CronJob normally starts at 06:10 UTC and must not overlap this window. Confirm that no prior renewal Job remains active before beginning.

## Entry checks

1. Confirm the operator, exact UTC window, customer impact and the separately approved production DDL/DML plan in the [Commerce preflight runbook](https://github.com/rbxrobotica/rbx-commerce/blob/main/docs/runbooks/satwake-commerce-migration-preflight.md). The corrected migration `000018` is in Commerce #66.
2. Recheck the effective database target, tracker `(5, false)`, the five `000018` candidate subscriptions and related tenant counts. Abort on drift. Confirm the three active historical Asaas licenses and the `000021` cutover decision.
3. Inspect the Asaas webhook queue before the window and arrange a post-window reconciliation. [Asaas' webhook FAQ](https://docs.asaas.com/docs/webhooks-faq) says only HTTP 200 confirms delivery and repeated failures can pause the queue after 15 attempts. Do not assume callbacks were delivered while the API was down.
4. Confirm no active Commerce checkout or operator operation is in progress. Keep paid traffic off. A fresh restricted backup and tracker recovery record are required before DDL/DML; the 2026-09-26 preparatory Commerce-only backup is not a substitute for a final maintenance-window snapshot.

On 2026-09-26 UTC, an authenticated read-only Asaas production query found one Commerce webhook at `commerce.rbx.ia.br/webhooks/asaas`, enabled and not interrupted, subscribed to 15 events. Kubernetes showed zero active renewal Jobs. These are point-in-time entry observations and must be checked again immediately before the window and after restoration.

## Controlled sequence

1. With the exact deploy approval, merge this PR and wait for Argo to sync. Verify API and web have zero ready pods, the renewal CronJob is suspended, no renewal Job is active and no affected writer remains. Stop if any writer is still live.
2. Take and verify the final backup. Run the separately approved, fail-closed atomic `000017`–`000023` migration and tracker reconciliation. On any failed precondition or transaction rollback, keep the application paused until the database state is verified.
3. After a successful commit, verify the actual schema, tracker, five tenant moves, related rows and cutover record before promoting the reviewed Commerce image in Infra #287. Do not resume the old API against an uncertain schema.
4. Restore API and web replicas through a follow-up GitOps change while leaving renewals suspended. Verify health, provider callback backlog, paid access and controlled checkout behavior. Resume renewals only through a separate reviewed change after its endpoint and ledger checks pass.

If a failure occurs after commit, keep the maintenance configuration until a data-aware repair is approved. A code image rollback does not reverse DDL, tenant moves or the payment-period cutover. No media spend or real purchase is part of this maintenance PR.
