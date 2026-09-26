# Satwake sandbox buyer identity contract

`satwake-buyer-sandbox.json` records the desired **separate** ZITADEL client
and machine-user settings for the inert product overlay in
`apps/prod/rbx-briefing-btc-sandbox`. It is a preparation contract, not a
ZITADEL resource applied by Ansible or GitOps. The existing
`zitadel-service-account-grants` role only grants an **already existing**
production machine user and updates a hardcoded Strategos client. It cannot
be reused for this sandbox without changing production identity.

The client ID, machine-user ID and project ID remain `null` because those
resources do not exist in this repository. The `.invalid` callback and
logout URLs cannot be registered as a published buyer journey. The BFF
remains scaled to zero and its policy URL remains loopback. The offline
validator checks these fail-closed conditions and the exact manifest
contract; it does not call ZITADEL, Kubernetes, Commerce, or `pass` by
default.

```bash
python3 scripts/validate-satwake-sandbox-identity.py
```

Before a separate, specifically approved ZITADEL provisioning operation:

1. Select a real sandbox HTTPS hostname, then review its exact
   `/briefing-btc/api/auth/callback` and logout paths. Register a dedicated
   public OIDC client with authorization code, PKCE S256, no client secret,
   and the token-exchange grant required by the BFF. Do not add these URLs
   to the production Strategos or Briefing client.
2. Create a dedicated sandbox Commerce machine user in the correct ZITADEL
   project, grant it only the audience/scopes used by the BFF, and retain
   its private-key JWT credential in the approved secret store. Do not
   borrow `rbx-session-bff-commerce` from production. Confirm that the
   actual signed token has the expected `sub`, audience and scopes. The BFF
   selects public-client token exchange when
   `RBX_COMMERCE_TOKEN_EXCHANGE_CLIENT_ID` is set, so it needs no Commerce
   client secret. Commerce currently skips token audience validation; a
   signed token with a wrong audience can still pass its OIDC middleware.
   Treat audience enforcement as a separate Commerce release gate before
   exposing buyer access. Store the verified `sub` in
   `rbx/identity/session-bff-commerce-sandbox/service-subject`; this is the
   input to Commerce's deterministic UUIDv5 tenant derivation.
3. Derive the public sandbox tenant from that actual token `sub`. The same
   value must be used by the sandbox checkout and consumption recorder; the
   BFF's authenticated `/access` and invite claim requests derive it from
   the service token. A random UUIDv4 cannot match. The source staging
   guard in `scripts/stage-satwake-sandbox-core-secrets.py` checks the two
   recorded `pass` entries. `--verify-pass` on the offline validator checks
   the same relation without printing either entry; it does **not** verify
   that the recorded subject equals a live token, so inspect the issued
   token independently before staging.
4. Add the dedicated source Secrets and narrow cross-namespace reader RBAC
   in a separate review, then verify ExternalSecrets `Ready=True`. In an
   isolated internal canary with the sandbox policy gateway, test
   missing-scope, unpaid, paid and cross-tenant decisions. Establish a passing
   wrong-audience rejection test after Commerce enforces audience. The BFF's
   Commerce entitlement gate is optional in the current binary: missing
   credential configuration disables it rather than failing startup. Before
   any public route, verify the running BFF has no
   `entitlement_gate_disabled` event and an unpaid sandbox session is denied
   Pro access. Register TLS/DNS/Ingress only in the separate activation
   sequence.

No IdP account, client, project grant, token, credential, Secret, DNS record
or deployment is created by this contract.
