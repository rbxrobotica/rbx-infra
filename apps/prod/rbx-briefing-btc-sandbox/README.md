# Briefing BTC isolated buyer sandbox (preparation only)

This directory prepares a separate web app and session BFF for a buyer journey
against **rbx-commerce-sandbox**. It has no Argo CD Application, Ingress,
Certificate, or source Secret RBAC, and both Deployments have `replicas: 0`.
Its origin and OIDC callback use the reserved `.invalid` domain, while the
authorization gateway points to loopback port 1. Rendering these manifests is
safe; merging this directory alone does not publish a login or start pods.
Do not sync or scale it as a release.

The product image uses the existing `/briefing-btc` base path. It asks its own
BFF for `/me`; the BFF uses a dedicated sandbox Commerce service token for
invite claim and access resolution. The web server, not the browser, sends
`edition.viewed` to the sandbox Commerce API with a separate service key.
The existing production app and BFF continue to use production Commerce and
are outside this overlay.

## Preconditions for a later activation PR

1. Provision a dedicated ZITADEL public PKCE client with an exact HTTPS
   callback on the chosen sandbox host at
   `/briefing-btc/api/auth/callback`. Provision a dedicated sandbox Commerce
   service account with `service:commerce.read` and
   `service:commerce.entitlement.claim`. Verify the actual issued service
   token's `sub`. Record it in
   `rbx/identity/session-bff-commerce-sandbox/service-subject`, and derive
   `COMMERCE_PUBLIC_TENANT_ID` with the two UUIDv5 operations in
   `rbx-commerce/internal/middleware/auth.go`. The tenant must be nonzero,
   distinct from production, and equal in the running sandbox Commerce API,
   its consumption recorder, and the BFF's authenticated requests. The
   staging guard in `scripts/stage-satwake-sandbox-core-secrets.py` verifies
   this relationship with the recorded subject; the actual token subject
   must also be checked independently. Do not reuse production credentials.
2. Create only these dedicated source Secrets in `rbx-ia-br` and a narrow
   `get` Role and RoleBinding for this namespace's
   `external-secrets-reader`: `rbx-briefing-btc-sandbox-oidc`
   (`RBX_SESSION_BFF_CLIENT_ID`), `rbx-briefing-btc-sandbox-commerce`
   (`RBX_COMMERCE_CLIENT_ID`, `RBX_COMMERCE_MACHINE_KEY_JSON`,
   `RBX_COMMERCE_AUDIENCE`),
   `rbx-briefing-btc-sandbox-consumption`
   (`COMMERCE_CONSUMPTION_SERVICE_KEY`), and
   `rbx-briefing-btc-sandbox-content` (S3 credentials, endpoint, bucket and
   prefix). The consumption key must also be mirrored to **only** the
   sandbox Commerce API. Verify all four ExternalSecrets `Ready=True` and
   their target property names before scaling either Deployment. The
   `ghcr-pull-secret` must be available in this namespace.
3. Select and validate an isolated fixture edition source. The S3 Secret
   supplies `BRIEFING_S3_ENDPOINT`, `BRIEFING_S3_BUCKET` and
   `BRIEFING_S3_PREFIX` explicitly so the app cannot silently use its
   production defaults. Keep browser artifact access behind its existing
   server-side Pro check. Verify the policy gateway's behavior with a
   sandbox tenant: anonymous/Free deny Pro, paid active seat allows Pro,
   expired/refunded/cross-tenant deny. Replace the inert gateway URL only
   after that gate passes.
4. In a separate reviewed PR, choose the real sandbox hostname, replace the
   reserved origin, callback and public-site links, register the callback
   with the PKCE client, add the host's Ingress/Certificate/DNS, and make
   Argo sync explicitly controlled. Confirm TLS, host-scoped cookies and
   `/briefing-btc` paths. Do not route a sandbox buyer to the production
   landing or product. Scale the BFF and web from zero only after the
   identity, policy, Secret, Commerce schema, content and routing gates.
5. A buyer identity with `email_verified=true` must have the same email as
   the pending paid invite in the sandbox tenant. Use the normal OIDC login
   and BFF claim path. Confirm Free before payment, Pro only after the Asaas
   sandbox provider confirmation and paid license, then mount a fixture Pro
   edition in a real browser. Check one `edition.viewed` event for the paid
   subscription after repeated opens, and zero events for unauthorized or
   cross-tenant attempts. A server-only POST or a mock does not prove
   published buyer consumption.

This overlay does not configure the sandbox Asaas callback, email sink,
checkout, content fixture, account, secrets, DNS, or provider state. Those
changes have separate reviews and approvals.
