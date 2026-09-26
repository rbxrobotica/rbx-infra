# Satwake Commerce sandbox rehearsal

This overlay prepares only `rbx-commerce-sandbox` for a controlled checkout
rehearsal. It does not set credentials, turn on email verification, configure
the Asaas webhook, promote an image, or apply database migrations. The
production `rbx-commerce` Deployment and Secret are outside this overlay.

Commerce #52 removed the CI path that wrote sandbox and production image pins
directly to Infra `main`. It was merged on 2026-09-25. Sandbox image promotion
now needs its own reviewed Infra change; the prepared pin is Infra #286.

## Live read-only preflight (2026-09-26 03:42 UTC)

- The effective sandbox `DATABASE_URL` resolves to
  `rbx-commerce-postgres.rbx-commerce-sandbox.svc.cluster.local/rbx_commerce_sandbox`.
  A `BEGIN READ ONLY` query in that database found no
  `public.schema_migrations`, `commerce.subscriptions`, `provider_invoices`,
  `asaas_payment_periods`, or `checkout_email_challenges`. This is an **initial
  bootstrap through `000001`–`000023`**, not a catch-up from the production
  schema. No database write was made by this preflight.
- The live sandbox pod is 1/1 on the old Commerce image
  `sha-61b781d6bfffc9b51497f18272d1c1c464a9b424` with `ASAAS_ENV=sandbox`.
  `/health` returns 200, but `/api/public/altcha-challenge` returns 500.
  The Deployment has no `COMMERCE_PUBLIC_TENANT_ID` environment variable.
- The source Secret `rbx-ia-br/rbx-commerce-sandbox-secrets` and its synced
  ExternalSecret contain only `DATABASE_URL`, `COMMS_API_URL`, `ASAAS_API_KEY`,
  and `ASAAS_WEBHOOK_TOKEN`. The three required properties below are absent.
  The Argo CD Application is Synced/Healthy with automated self-heal, so a
  merge of this overlay before preparing the source Secret can roll a pod
  whose mandatory references cannot resolve. Secret values were not printed.
- The Asaas sandbox account's sole webhook points to
  `https://commerce-sandbox.rbx.ia.br/webhooks/asaas`, but is `enabled=false`
  and `interrupted=true`. Its event set includes `PAYMENT_CONFIRMED` and
  `PAYMENT_RECEIVED`. The read-only listing does not return its authentication
  token, so it cannot establish a token match with the receiver.

An independent read-only check at 2026-09-26 06:23 UTC again found no
`commerce` schema, no `public.schema_migrations`, and no Commerce tables in
`rbx_commerce_sandbox`. The sandbox API was still on the old image and the
Argo CD Application was Synced/Healthy. Its active database session uses the
`rbx_commerce_sandbox` role; that role has `CONNECT` and `CREATE` on the
database and `USAGE` and `CREATE` on `public`. The database itself is owned by
`postgres`, so run the migrations as the sandbox runtime role and verify the
resulting object ownership and API grants. This is a point-in-time preflight,
not a migration or a frozen write window.

## Configuration before this overlay is reconciled

First deploy and verify [Infra #306](https://github.com/rbxrobotica/rbx-infra/pull/306).
The **running** Commerce sandbox pod must have
`COMMS_API_URL=http://127.0.0.1:1`, and its ExternalSecret must no longer map
the production Comms URL. This baseline remains in place throughout this
overlay's rollout. The public `/checkout/email/start` route is mounted even
when `SATWAKE_EMAIL_VERIFICATION_REQUIRED` is unset, so the flag does not
prevent email dispatch to a configured Comms endpoint. Do not add a service
key, Altcha Secret, or promote an email-capable Commerce image while the
effective URL still points to production Comms. Reverting #306 to the legacy
Secret reference is not an allowed rollback.

Obtain separate approval for the sandbox source Secret change. Add the three
properties below to **`rbx-ia-br/rbx-commerce-sandbox-secrets`**, preserving its
existing properties. The ExternalSecret copies only from that named source
Secret into `rbx-commerce-sandbox/rbx-commerce-sandbox-secrets`; it never reads
`rbx-commerce-secrets` (production). All three pod references are mandatory, so
a missing property prevents a new sandbox pod from becoming ready.

| Property | Requirement |
| --- | --- |
| `ALTCHA_SECRET` | Nonempty sandbox-only signing secret; do not copy the production Commerce value. |
| `COMMS_SERVICE_API_KEY` | New sandbox-only key shared with the isolated email sink through [Infra #307](https://github.com/rbxrobotica/rbx-infra/pull/307). Use an unrelated value of at least 32 bytes; never copy the production Comms service key. |
| `COMMERCE_PUBLIC_TENANT_ID` | The exact UUIDv5 that Commerce derives from the `sub` of a token **issued to a dedicated sandbox buyer BFF service account**: `UUIDv5(UUIDv5(NAMESPACE_URL, "https://rbx.ia.br/tenant"), sub)`. It must differ from zero and the production public tenant. A random UUIDv4, a copied production tenant, or a value inferred from an unissued subject will not establish the buyer's authenticated tenant. Infra #315 checks this relationship before staging the source Secret; it does not create the service account. |

Provision a second, unrelated `SATWAKE_EMAIL_SINK_OPERATOR_KEY` of at least
32 bytes for the sink's loopback-only claim route. Infra #307 maps the two
source properties to separate runtime Secrets; [Infra #304](https://github.com/rbxrobotica/rbx-infra/pull/304)
deploys the sink workload only after both ExternalSecrets are Ready. Verify
the sink and live NetworkPolicy enforcement before this overlay is merged.
This overlay must not restore a shared production Comms URL or key. A separate
later PR may cut over from the inert URL to the ready sink after this overlay
is healthy; its rollback returns to the inert URL.

The source Secret change must be prepared before merging this overlay because
the Commerce sandbox Argo CD Application synchronizes automatically. Review
the resulting ExternalSecret `Ready` condition and target Secret **key names
only**; do not print secret values. A healthy `/health` alone says nothing
about the new checkout route, migrations, or provider callback.

Infra #301 changed the normal Ansible update to preserve additional source
Secret properties, but creating a new source Secret from scratch still omits
the four Satwake properties (`ALTCHA_SECRET`, `COMMS_SERVICE_API_KEY`,
`COMMERCE_PUBLIC_TENANT_ID`, and `SATWAKE_EMAIL_SINK_OPERATOR_KEY`). Record
the approved values in the operator secret store and re-provision them after
source Secret recreation, before rolling Commerce or the sink. A synced
ExternalSecret target is not a durable source.

## Code and database gates

1. Reconfirm the effective `DATABASE_URL` target, without printing the
   password, immediately before migration. With sandbox API and callback
   writers quiesced through a reviewed GitOps hold and a restricted backup
   restored in a temporary database, bootstrap the **sandbox database only**
   under a separately approved, schema-aware plan. The corrected Commerce
   `000013` and the staged `000001`–`000017`, zero-row gate, then
   `000018`–`000023` sequence are documented in the
   [Commerce sandbox bootstrap runbook](https://github.com/rbxrobotica/rbx-commerce/blob/main/docs/runbooks/satwake-commerce-sandbox-bootstrap.md).
   Verify the resulting objects, ownership or runtime grants, and tracker
   `(23, false)` before Infra #286 pins the new Commerce image. Do not promote
   that image against the empty schema. Also follow the read-only
   [Commerce migration preflight](https://github.com/rbxrobotica/rbx-commerce/blob/main/docs/runbooks/satwake-commerce-migration-preflight.md),
   including verification of `000015`–`000018`. `000018` hardcodes the **production**
   public tenant as its data-move target. Never use that tenant for this
   sandbox: inspect eligible zero-tenant subscriptions and invites, existing
   `000018` markers, and related-row tenant consistency before deciding whether
   the migration is a no-op here or needs a separate sandbox data repair plan.
   Quiesce writes to all affected Commerce tables, including checkout,
   invites, provider callbacks, reconciliation and seat assignment, or use a
   reviewed transactionally safe exclusion. Preserve provider deliveries for
   retry. Repeat the eligible-row and related-row checks immediately before
   `000018` DML, then resume writes only after migration and the exact
   subject-derived sandbox tenant configuration are verified. A prior zero
   count alone is not a safe no-op guarantee while the old API can still write
   zero-tenant records.
   Do not replay it blindly or advance the tracker merely to match a version.
   The live sandbox Deployment inspected on 2026-09-26 does not define
   `COMMERCE_PUBLIC_TENANT_ID`, so its current API uses the zero tenant until
   the source Secret and this overlay are reconciled. Verify the actual schema
   after application; do not rely solely on the historical
   `schema_migrations` tracker. Never run this against production.
2. Only after the required sandbox schema is verified, promote the reviewed
   Commerce image containing the Satwake checkout, acquisition, authenticated
   webhook, paid-period, pause/cancel, and buyer email verification changes
   (Commerce PRs #43, #45, #46, #48, #49, #50, #53, and their #44/#47
   integration), plus the Asaas sandbox API host and first-charge due-date
   fixes (#54 and #55). Keep `ASAAS_ENV=sandbox`.
3. Confirm #306 keeps the effective Commerce URL inert. Reconcile #307's two
   sandbox-only keys, then deploy #304's sink and verify its dispatch and
   loopback operator routes with reserved `example.invalid` addresses. A
   request without the service key must return `401`; a valid sandbox request
   returns `202`, and a code can be claimed once. This proves only isolated
   acceptance, not real mailbox delivery. Then reconcile this overlay's
   Commerce key and tenant properties while the URL stays inert. Check that
   the configured Commerce tenant equals the tenant derived from an **issued**
   token of the dedicated sandbox BFF service account, and that this BFF calls
   the sandbox Commerce API. Move the URL to the sink only in a separate,
   approved cutover after all those checks.
4. Keep `SATWAKE_EMAIL_VERIFICATION_REQUIRED` unset until the checkout UI,
   sink, key, and migration are verified together. This flag does not gate
   `/checkout/email/start`. Activating the flag for the isolated sandbox is
   a separate reviewed configuration change. Activate it before testing
   rejection of an unverified checkout.
5. `GET /api/public/altcha-challenge` must return a challenge. Exercise a
   browser Origin expected by the checkout policy; Satwake campaign hosts
   need an exact active Commerce campaign registry entry. A direct API call
   cannot prove browser CORS or paid attribution. The Landing #4 image
   defaults to `https://commerce.rbx.ia.br`; use an isolated build or preview
   explicitly configured for the sandbox Commerce base URL for browser E2E.
   Do not use the production landing image as evidence of a sandbox checkout.

## Provider and access rehearsal

The sandbox Asaas webhook targeting `commerce-sandbox.rbx.ia.br` was still
disabled and interrupted in the 2026-09-26 read-only API inspection. Enabling
or resuming it, or changing its token or destination, needs a separate approved
provider-account operation. Confirm the configured `asaas-access-token`
matches the sandbox receiver before relying
on delivery; do not point this webhook at production Commerce.

With the callback approved and enabled, use fictitious sandbox customer data
and a reserved test address handled only by the sink. Record one rejected unverified checkout, one
verified R$39 Pix checkout, its immutable attempt retry, the Asaas subscription
and first charge, the authenticated raw webhook, the paid-period ledger, the
dated entitlement, and the matching sandbox product access decision. Confirm
the receipt of `PAYMENT_CONFIRMED` or `PAYMENT_RECEIVED` before calling access
paid. Use Asaas sandbox payment confirmation only for this isolated test; no
real payment or production customer is needed. Then exercise a duplicate
callback, renewal period, pause/resume, cancellation and refund separately,
checking that an unpaid/fully refunded period grants no access and an already
paid period ends at its recorded `paid_until`.

This overlay alone cannot prove product access: the product/BFF test surface
must query the sandbox Commerce tenant and database. It also cannot prove a
Meta delivery receipt, campaign attribution, or a production purchase. Keep
paid media off until those release gates are explicitly completed.
