# Satwake sandbox buyer identity contract

`satwake-buyer-sandbox.json` records the desired **separate** ZITADEL client
and machine-user settings for the inert product overlay in
`apps/prod/rbx-briefing-btc-sandbox`. It is a preparation contract, not a
ZITADEL resource applied by Ansible or GitOps. The existing
`zitadel-service-account-grants` role only grants an **already existing**
production machine user and updates a hardcoded Strategos client. It cannot
be reused for this sandbox without changing production identity.

The contract's client, machine-user and project IDs remain `null` because the
overlay is not activated; the provider-side resources are recorded below.
The `.invalid` callback and
logout URLs cannot be registered as a published buyer journey. The BFF
remains scaled to zero and its policy URL remains loopback. The offline
validator checks these fail-closed conditions and the exact manifest
contract; it does not call ZITADEL, Kubernetes, Commerce, or `pass` by
default.

Provider preflight on 2026-09-27 found that the project ID
`375992756025295047` in the current production Commerce audience is named
`ZITADEL`, not Commerce. It contains the ZITADEL administrative apps and the
existing Briefing BTC session client; no owned project named Commerce appeared
in the project inventory. Do not treat that audience as authorization to create
sandbox resources in an existing Commerce project. Under separate operator
authorization, the isolated project `rbx-satwake-sandbox`
(`392558889029076638`), public PKCE app (`392558943051712158`), and machine
user (`392559040594444404`) were created. The public client has only the
authorization-code grant. A JWT from the dedicated machine user passed local
signature, issuer, `sub`, audience, and the two project-role checks; its OAuth
`scope` and `scp` claims were absent. The subject and derived UUIDv5 tenant
were stored in `pass`. After separate approval, the tenant was also staged in
the Commerce sandbox **source** Secret; identity credentials have not been
staged in Kubernetes, and the runtime mapping remains a draft.
The candidate host `briefing-btc-sandbox.rbx.ia.br` had no A record or Ingress
at preflight; neither is configured by this contract.

Durable entries created in `pass` are
`rbx/identity/briefing-btc-sandbox/oidc-client-id`,
`rbx/identity/session-bff-commerce-sandbox/{client-id,machine-key-json,audience,service-subject}`,
and `rbx/commerce-sandbox/public-tenant-id`. The machine key expires on
2026-12-26 UTC. Do not copy its value into a PR, log, or terminal output.

```bash
python3 scripts/validate-satwake-sandbox-identity.py
```

Before activating the prepared identity in any workload:

1. Verify the registered sandbox callback and logout paths against the chosen
   HTTPS hostname, and replace the inert `.invalid` overlay only in a separately
   approved activation. The public client uses authorization code, PKCE S256,
   no client secret and no token-exchange grant. Do not add these URLs to the
   production Strategos or Briefing client.
2. Keep the dedicated sandbox Commerce machine user on
   `ACCESS_TOKEN_TYPE_JWT` and only the two Commerce roles in this contract.
   Its private-key JWT credential is in the approved secret store. Do not
   borrow `rbx-session-bff-commerce` from production.
   Confirm that the provider actually issues a signed JWT with the expected
   `sub`, audience and project-specific roles claim. ZITADEL did not put the
   requested custom strings in `scope` or `scp` on 2026-09-27, so the BFF
   requests the reserved roles scope and a sandbox-only Commerce adapter must
   validate that signed claim before mapping the two roles to Commerce scopes.
   The BFF can consume a JWT from its machine key
   directly; it needs neither a Commerce client secret nor public-client token
   exchange. ZITADEL [recommends confidential clients for token exchange](https://zitadel.com/docs/guides/integrate/token-exchange).
   Commerce #78 added an opt-in exact audience/role adapter. This draft pins
   both `OIDC_ALLOWED_AUDIENCES` and `OIDC_ZITADEL_ROLE_PROJECT_ID` to the
   isolated project, while keeping the API at zero replicas. Production's
   unconfigured default remains unchanged. Verify live 401/403 rejections
   after the reviewed sandbox image is deployed and before buyer access. The
   verified `sub` is stored in
   `rbx/identity/session-bff-commerce-sandbox/service-subject`; this is the
   input to Commerce's deterministic UUIDv5 tenant derivation.
3. Confirm the stored public sandbox tenant against that actual token `sub`. The same
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
