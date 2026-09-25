# Satwake buyer email verification rollout

The Commerce code defaults `SATWAKE_EMAIL_VERIFICATION_REQUIRED` to off. This
configuration PR mirrors an already prepared Comms service key into the
Commerce namespace and exposes it to the pod. It does not activate the public
checkout gate. The Commerce ArgoCD Application has auto-sync/self-heal, so
merging this PR can immediately roll the Deployment.

## Dependencies and order

1. Merge the direct-deploy release gates before merging buyer-facing code:
   Commerce #52, Market Briefing #13, Briefing BTC #5, and Systems Frontend
   #100. Each merge and later image pin needs its own approval.
2. Merge and deploy Comms #19. Confirm that its service-key-protected email
   endpoint returns Postmark acceptance in an isolated test. A 202 is not
   proof of mailbox delivery.
3. Merge Commerce #50 and then #53. Apply migration `000023` under a separate
   production-migration approval. Deploy the new Commerce image with the
   verification flag still off.
4. **Before merging this mapping PR**, under a separate production-secret
   approval, run the narrow operation prepared by the Satwake Commerce key
   source PR. It patches only `COMMS_SERVICE_API_KEY` in the source
   `rbx-ia-br/rbx-commerce-secrets`; confirm key presence without printing it.
   Do not run the broad `k8s-secrets` role here: its current Comms source task
   omits the active Meta keys. Confirm those Meta keys and Comms health remain
   intact. Once the source key exists, a specifically approved merge of this
   mapping PR lets ExternalSecret sync it and ArgoCD roll Commerce. Verify
   ExternalSecret readiness and the new Commerce pod before proceeding.
   Before enabling the flag, check the copied key against **each running
   Comms replica**, not only the Secret object: port-forward to each ready
   Comms pod and send `POST /api/v1/outbound/satwake-email-code` with the
   Commerce key and JSON `{}`. A matching live service key returns HTTP 422
   for the invalid body; a missing or stale key returns HTTP 401. Keep the
   credential in process memory, never in a shell argument, URL, file, or
   log. This validation does not send an email. If a replica fails, reconcile
   the Comms key and rollout before activation. Recheck after either service
   key rotates.
5. Deploy both buyer interfaces: the satwake landing email-verification PR and
   the institutional frontend email-verification PR. Verify the same opaque
   challenge ID passes through start, verify, checkout, and recovery.
6. Build an isolated integration environment with a disposable PostgreSQL
   database, the branch Commerce and Comms binaries, a test email sink, and
   Asaas HTTP fixtures. The existing `rbx-commerce-sandbox` namespace does
   **not** have `ALTCHA_SECRET`, the Comms service key or the public tenant
   contract, so it cannot serve as this gate without another approved
   configuration change. Before any live Asaas sandbox observation, include
   Commerce #54, which corrects the sandbox API host to
   `api-sandbox.asaas.com`; the previous host returned HTML to an authenticated
   read-only API request. Include Commerce #55 before testing the financial
   flow: Asaas ignored the old `dueDate` property, while its documented
   `nextDueDate` generated a first charge due on the next Brazilian calendar
   day. Test one challenge email, a new pending Pix,
   idempotent retry, a lost-browser recovery, wrong-code/expired-code limits,
   and a paid or changed Asaas invoice that refuses recovery. Check that the
   email address and code do not appear in request logs or Comms persistence.
   Observe an Asaas sandbox flow separately before production activation. The
   sandbox webhook was disabled at the 2026-09-25 read-only check, so provider
   subscription and payment probes alone cannot verify Commerce webhook
   reconciliation; callback configuration requires a separate approval.
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
