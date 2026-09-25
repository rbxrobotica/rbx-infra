# Satwake Commerce sandbox rehearsal

This overlay prepares only `rbx-commerce-sandbox` for a controlled checkout
rehearsal. It does not set credentials, turn on email verification, configure
the Asaas webhook, promote an image, or apply database migrations. The
production `rbx-commerce` Deployment and Secret are outside this overlay.

Before merging any Satwake Commerce feature, separately approve and merge
Commerce #52. Its current `main` CI still writes the sandbox **and production**
image pins directly to Infra `main`; #52 removes that automatic deployment
path. Sandbox image promotion then needs its own reviewed Infra change.

## Configuration before this overlay is reconciled

Obtain separate approval for the sandbox source Secret change. Add the three
properties below to **`rbx-ia-br/rbx-commerce-sandbox-secrets`**, preserving its
existing properties. The ExternalSecret copies only from that named source
Secret into `rbx-commerce-sandbox/rbx-commerce-sandbox-secrets`; it never reads
`rbx-commerce-secrets` (production). All three pod references are mandatory, so
a missing property prevents a new sandbox pod from becoming ready.

| Property | Requirement |
| --- | --- |
| `ALTCHA_SECRET` | Nonempty sandbox-only signing secret; do not copy the production Commerce value. |
| `COMMS_SERVICE_API_KEY` | Key accepted by the Comms instance in this sandbox's `COMMS_API_URL`. If the existing shared Comms service is used, approve that controlled sandbox-to-Comms access and limit the rehearsal to a controlled mailbox. Do not use a key from an unrelated instance. |
| `COMMERCE_PUBLIC_TENANT_ID` | Nonzero UUID reserved for this isolated sandbox journey. Configure any sandbox product/BFF access check for this same tenant; never use the production public tenant UUID. |

Using the shared Comms service and its existing service key does not isolate
that service from production. Treat its broader access as an explicit part of
the approved rehearsal scope; this overlay does not narrow Comms permissions.

The source Secret change must be prepared before merging this overlay because
the Commerce sandbox Argo CD Application synchronizes automatically. Review
the resulting ExternalSecret `Ready` condition and target Secret **key names
only**; do not print secret values. A healthy `/health` alone says nothing
about the new checkout route, migrations, or provider callback.

The current Ansible `k8s-secrets` role recreates this source Secret with only
its four original fields. A rebootstrap therefore removes these three new
properties. Record the approved sandbox values in the operator secret store
and reapply them after any role run, before rolling Commerce; do not assume
that a prior ExternalSecret sync makes the source durable. Update the role in
a separate reviewed change before relying on this as a repeatable environment.

## Code and database gates

1. Promote a reviewed Commerce image containing the Satwake checkout,
   acquisition, authenticated webhook, paid-period, pause/cancel, and buyer
   email verification changes (Commerce PRs #43, #45, #46, #48, #49, #50,
   #53, and their #44/#47 integration), plus the Asaas sandbox API host and
   first-charge due-date fixes (#54 and #55). Keep `ASAAS_ENV=sandbox`.
2. Inspect the **sandbox database only** and apply its missing Commerce
   migrations through `000023` in order under an approved migration plan.
   First parse the effective `DATABASE_URL` without printing the password and
   verify its database is `rbx_commerce_sandbox` on the sandbox PostgreSQL
   service. Verify the actual schema after application; do not rely solely on the
   historical `schema_migrations` tracker. Never run this against production.
3. Deploy Comms #19 and confirm the instance named by `COMMS_API_URL` has a
   working Postmark sender. Probe the configured service key against its
   Satwake email route with invalid JSON and no destination: an authenticated
   validation error must differ from `401`, without sending an email. Then
   use a controlled inbox for the actual challenge test.
4. Keep `SATWAKE_EMAIL_VERIFICATION_REQUIRED` unset until the checkout UI,
   Comms sender, key, and migration are verified together. Activating this
   flag for the isolated sandbox is a separate reviewed configuration change;
   activate it before testing rejection of an unverified checkout.
5. `GET /api/public/altcha-challenge` must return a challenge. Exercise a
   browser Origin expected by the checkout policy; Satwake campaign hosts
   need an exact active Commerce campaign registry entry. A direct API call
   cannot prove browser CORS or paid attribution.

## Provider and access rehearsal

The sandbox Asaas webhook targeting `commerce-sandbox.rbx.ia.br` was disabled
in the 2026-09-25 read-only inspection. Enabling it or changing its token or
destination needs a separate approved provider-account operation. Confirm the
configured `asaas-access-token` matches the sandbox receiver before relying
on delivery; do not point this webhook at production Commerce.

With the callback approved and enabled, use fictitious sandbox customer data
and a controlled email address. Record one rejected unverified checkout, one
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
