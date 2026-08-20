# Strategos production

Domain vocabulary, execution artifact names, events, RPC names, and migration
semantics for Strategos are governed by the canonical contracts in
`strategos-core/docs/architecture/`, especially:

- `VOCABULARY-CONTRACT.md`
- `EXECUTION-ARTIFACT-NAMING-CONTRACT.md`
- `STRATEGOS-EXECUTION-INDEX.md`

Deployment documentation in rbx-infra should reference those contracts instead
of inventing new Strategos domain terms.

Strategos UI is exposed through the Merovelis app host with legacy aliases during
the migration:

- `app.merovelis.com`
- `strategos.rbx.ia.br`
- `strategos.rbxsystems.ch`

The canonical authenticated Strategos entrypoint is `app.merovelis.com/strategos`
per the Merovelis border map. `app.rbxsystems.ch/strategos` remains the
transition alias until the Merovelis route is fully adopted.

On the app host, `/strategos` routes to Strategos UI. `/api/auth/*` is also
routed to Strategos UI so the product-local SvelteKit auth adapter can proxy
login, callback, session, and logout to `rbx-session-bff` without exposing
browser tokens.

## Central de Planos access

The Plans module is a read-only projection of the generated
`rbx-governance/plans-index.json` contract. Governance remains the canonical
record; Strategos does not edit plans or become a parallel registry.

The initial production release uses the image-bundled Governance snapshot. The
UI must label that source as `unverified`; no private-repository token or
remote source is configured by this release.

Access requires all of the following:

- an active Strategos Commerce entitlement;
- a successful server-side policy decision;
- the exact `rbx:plans:viewer` permission in the product session;
- a ZITADEL `rbx:plans:viewer` project role issued by organization
  `375992756025229511`.

The operator-approved audience is all current RBX founders. Founder status does
not grant access by itself: each exact ZITADEL subject must be explicitly
assigned the project role through a separate human-gated identity operation.
Existing browser sessions must be replaced after assignment so the new ID token
contains the role. Urgent removal requires removing the role and revoking the
product-local session.

`app.merovelis.com` and `auth.merovelis.com` are managed in
`infra/terraform/dns/merovelis.com.tf`.
`app.rbxsystems.ch` is kept in the `rbxsystems.ch` zone as a transition alias.
`strategos.rbxsystems.ch` is managed in `infra/terraform/dns/rbxsystems_ch.tf`.
The `rbx.ia.br` zone is currently documented as externally managed, so
`strategos.rbx.ia.br` must be created where the existing `robson.rbx.ia.br`
record is managed and pointed at the k3s ingress IP (`158.220.116.31`).

The UI image is published manually by the
`rbxrobotica/strategos-ui` GitHub Actions workflow as
`ghcr.io/rbxrobotica/strategos-ui:sha-<short-sha>`. The same reviewed workflow
may open a promotion PR, but it does not merge or deploy that proposal.

The container package is private because the source repository is private. The
namespace uses the out-of-band `ghcr-pull-secret`
`kubernetes.io/dockerconfigjson` secret with `read:packages` access. Do not
commit that token to Git.
