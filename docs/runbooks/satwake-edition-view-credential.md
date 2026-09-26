# Satwake edition-view credential: release gate

This proposal wires `COMMERCE_CONSUMPTION_SERVICE_KEY` to the Commerce API and
the Briefing BTC web server. The product uses it to sign a short-lived edition
page token and to call `POST /api/v1/consumption/edition-views`. Commerce uses
the same value to authenticate that route. It records an `edition.viewed`
event only after checking the subscription-origin paid license and assigned
seat. A page open is not proof that a person read the whole edition.

## Credential boundary

Create a new, high-entropy credential for this route. Do not reuse
`COMMERCE_SERVICE_KEY`: that key also authorizes subscriber-phone export,
renewal runs and other service endpoints. The product should receive only
the consumption key. The source Secret must contain only the
`COMMERCE_CONSUMPTION_SERVICE_KEY` property, named
`rbx-ia-br/rbx-commerce-consumption-key`. External Secrets copies that
property into separate dedicated Secrets in the Commerce and Briefing BTC
namespaces. Neither existing workload Secret is changed.
Kubernetes RBAC grants the Briefing BTC reader access only to the dedicated
source Secret, not to `rbx-ia-br/rbx-commerce-secrets`. The Commerce reader's
Role and RoleBinding are rendered by the `rbx-ia-br` overlay so that the
grant remains in the source namespace; the Commerce overlay owns only its
reader ServiceAccount.

No key value or Secret is created by this repository change.

## Approval and rollout order

1. Obtain explicit approval for a production credential change. Generate a
   fresh random key in the operator's secret store and prepare the dedicated
   source Secret. Do not print the key in shell output, logs or PR comments.
   Verify that the source Secret contains exactly the expected property. Keep
   its recovery procedure in the approved secret store so a cluster rebuild
   can recreate the same source before either workload starts.
2. **Stage 1, this Infra change:** obtain separate approval for its merge and
   GitOps sync. It adds only source read grants and two dedicated
   ExternalSecrets. It does not add environment references or change either
   Deployment. Check that both ExternalSecrets report `Ready=True` and that
   both target Secrets contain the expected **key name only**. If either
   mirror is unready, stop; do not merge stage 2.
   Check the rendered and live `read-commerce-secret` Role in `rbx-ia-br`:
   the Commerce reader must be able to get the dedicated source Secret there.
   The Briefing BTC reader must be able to get that source but must not be able
   to get `rbx-commerce-secrets`. These checks inspect authorization only,
   never Secret values.
3. Merge and promote the reviewed Commerce consumption code (#44) and Briefing
   BTC page-open code (#4), after their respective direct-deploy CI release
   gates (#52 and #5). Pin each image through a separately approved Infra PR.
4. **Stage 2, a separate Infra PR:** only after stage 1 mirrors are Ready and
   the reviewed images are available, add mandatory
   `COMMERCE_CONSUMPTION_SERVICE_KEY` environment references to both
   Deployments and `COMMERCE_BASE_URL` to the product. Obtain separate approval
   for this merge/GitOps rollout. Without `COMMERCE_BASE_URL`, the page-open
   recorder returns failure. A missing target key can prevent pod readiness.
5. In a controlled, authorized test, use a genuine published manifest and a
   paid, dated, subscription-origin Pro license. Open the edition through the
   product BFF. Confirm one `edition.viewed` event with the expected tenant,
   subscription ID, edition date, locale, license and subject. Reopen it and
   confirm idempotency. Confirm Free and invalid-license opens do not produce
   a paid-consumption event. Keep this controlled test outside commercial
   acquisition and CAC counts.

The two services read the key from environment variables at process start.
Plan key rotation as a coordinated rollout; changing only the source Secret
does not update already-running pods and may temporarily reject view receipts.
If deployment or test fails, leave the consumption claim unverified and
investigate the source, ExternalSecret readiness, pod key mapping and HTTP
authorization before retrying. Do not infer consumption from pageviews or
Comms delivery receipts.
