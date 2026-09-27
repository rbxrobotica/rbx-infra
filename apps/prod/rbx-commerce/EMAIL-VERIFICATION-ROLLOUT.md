# Satwake buyer email verification rollout

The Commerce code defaults `SATWAKE_EMAIL_VERIFICATION_REQUIRED` to off. Infra
#281 already mirrored the Comms service key into Commerce and exposed it to the
pod. The public checkout gate remains off: the production Commerce Deployment
does not set `SATWAKE_EMAIL_VERIFICATION_REQUIRED`. The Commerce ArgoCD
Application has auto-sync/self-heal, so a future activation PR can immediately
roll the Deployment.

## Verified checkpoint, 2026-09-26

- Production Commerce API and web Deployments were each 2/2. Commerce references
  `COMMS_API_URL` and `COMMS_SERVICE_API_KEY` from `rbx-commerce-secrets`.
  Comms was 2/2 with `POSTMARK_SERVER_TOKEN` and `COMMS_SERVICE_KEY` referenced
  from its Secrets. These checks establish wiring, not provider delivery.
- Each ready Comms pod was reached separately by localhost port-forward. For
  `POST /api/v1/outbound/satwake-email-code` with the invalid body `{}`, no key
  returned HTTP 401 and the existing Commerce service key returned HTTP 422.
  No recipient or code was supplied, so the handler did not call Postmark. The
  key was held only in process memory and its value was not logged or printed.
- The Comms #288 image and Commerce database migrations `000017`–`000023` are
  deployed. Prior local Asaas sandbox rehearsal confirmed one fictitious Pix
  payment, paid entitlement, buyer claim and an idempotent `edition.viewed`.
  It did not test a remote provider callback or a delivered verification email.
- The cluster Commerce sandbox still has an inert `COMMS_API_URL` of
  `http://127.0.0.1:1`; it lacks its public tenant, `ALTCHA_SECRET`, dedicated
  identity and schema. Key mirrors alone do not make this a live email/checkout
  test environment. Do not point it at production Comms as a shortcut.

The next gates are a controlled email sink or explicitly approved test email,
browser challenge and recovery against an isolated Commerce instance, provider
callback and reconciliation, and a separately approved activation of the flag.

## Dependencies and order

1. The direct-deploy release gates in Commerce #52, Market Briefing #13,
   Briefing BTC #5 and Systems Frontend #100 were merged before buyer-facing
   code. Their later image pins remain separate deployment decisions.
2. Comms #19 and the newer Comms #288 image were deployed. The key check above
   passed, but the service-key-protected email endpoint still needs an
   explicitly approved positive Postmark test and mailbox observation. A 202
   is provider acceptance, not proof of mailbox delivery.
3. Commerce #50, #53, #54 and #55 were merged; the corrected Asaas sandbox host
   and first-charge `nextDueDate` behavior are in the deployed Commerce image.
   Migration `000023` was applied with the approved production schema window.
   The email-verification flag remains off.
4. The narrow source-key patch and Infra #281 rollout are complete. The copied
   key passed the per-pod 401/422 check described above. Keep the credential in
   process memory, never in a shell argument, URL, file, or log. Recheck each
   ready replica after either service key rotates or Comms rolls again. Do not
   run the broad `k8s-secrets` role merely for this key: its Comms source task
   must preserve the active Meta credentials.
5. The landing and institutional frontend email-verification code is merged,
   but their new images have not both been promoted. Deploy both under their
   separate release gates. Verify the same opaque challenge ID passes through
   start, verify, checkout, and recovery.
6. Build an isolated integration environment with a disposable PostgreSQL
   database, the branch Commerce and Comms binaries, a test email sink, and
   Asaas HTTP fixtures. The existing `rbx-commerce-sandbox` namespace cannot
   serve as this gate yet: its URL is deliberately inert, and its public
   tenant, `ALTCHA_SECRET`, dedicated identity and schema are not prepared.
   Its Comms key mirrors do not change that. The image used for the Asaas
   sandbox exercise contains Commerce #54, which corrects the sandbox API host
   to `api-sandbox.asaas.com`, and #55, which uses `nextDueDate` for the first
   charge. Test one challenge email, a new pending Pix,
   idempotent retry, a lost-browser recovery, wrong-code/expired-code limits,
   and a paid or changed Asaas invoice that refuses recovery. Check that the
   email address and code do not appear in request logs or Comms persistence.
   Before production activation, run a controlled Commerce-to-Asaas sandbox
   checkout with an authenticated provider callback. Verify deduplication,
   payment/status reconciliation, the dated entitlement, and pause/deletion
   behavior through Commerce. The sandbox webhook was disabled at the
   2026-09-25 read-only check; enabling or changing callback configuration
   requires a separate approval. Direct provider probes and HTTP fixtures do
   not satisfy this gate. Keep the verification flag off while the callback is
   disabled or any reconciliation check remains unverified.
7. Prepare a separate Infra change setting
   `SATWAKE_EMAIL_VERIFICATION_REQUIRED=true`. Request specific approval for
   that activation only after steps 1–6 pass. Confirm both interfaces use the
   new flow before enabling it globally for BRL/Pix.

If the source `rbx-ia-br/rbx-commerce-secrets` is recreated, reapply the
approved narrow key patch before any Commerce rollout and monitor
ExternalSecret and pod readiness. The current `k8s-secrets` role does not
declare this property; incorporate it into that role only when its Comms
source task safely retains the active Meta credentials.

If the email endpoint or Asaas live check fails, keep the flag off and use the
existing operator reconciliation path for legacy pending invoices. Disabling
the flag again after activation affects new checkouts; it does not delete or
cancel existing provider invoices or verified challenge records.
