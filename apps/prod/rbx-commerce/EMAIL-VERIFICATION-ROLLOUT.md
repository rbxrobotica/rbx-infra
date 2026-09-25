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
5. Deploy both buyer interfaces: the satwake landing email-verification PR and
   the institutional frontend email-verification PR. Verify the same opaque
   challenge ID passes through start, verify, checkout, and recovery.
6. Build an isolated integration environment with a disposable PostgreSQL
   database, the branch Commerce and Comms binaries, a test email sink, and
   Asaas HTTP fixtures. The existing `rbx-commerce-sandbox` namespace does
   **not** have `ALTCHA_SECRET`, the Comms service key or the public tenant
   contract, so it cannot serve as this gate without another approved
   configuration change. Test one challenge email, a new pending Pix,
   idempotent retry, a lost-browser recovery, wrong-code/expired-code limits,
   and a paid or changed Asaas invoice that refuses recovery. Check that the
   email address and code do not appear in request logs or Comms persistence.
   Observe an Asaas sandbox flow separately before production activation.
7. Prepare a separate Infra change setting
   `SATWAKE_EMAIL_VERIFICATION_REQUIRED=true`. Request specific approval for
   that activation only after steps 1–6 pass. Confirm both interfaces use the
   new flow before enabling it globally for BRL/Pix.

If the email endpoint or Asaas live check fails, keep the flag off and use the
existing operator reconciliation path for legacy pending invoices. Disabling
the flag again after activation affects new checkouts; it does not delete or
cancel existing provider invoices or verified challenge records.
