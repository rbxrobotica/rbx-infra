# rbx-grant-reconciler

Guarantees the baseline ZITADEL project grant for human users, so that a
federated login (Google included) never leaves someone authenticated with an
empty `urn:zitadel:iam:org:project:roles` claim.

Source and design: `rbx-identity/services/rbx-grant-reconciler`, ADR-0007, and
`rbx-identity/docs/runbooks/baseline-grant-automation.md`.

## The invariant

The baseline grant means "known user" and confers no product permission.
Product access is a licence in `rbx-commerce`, read through
`rbx-policy-gateway`. If any product ever maps a baseline role to a permission,
open self-registration stops being safe. That is the thing to defend in review.

## Bring-up order

1. Merge the service in `rbx-identity` so the image is published, then add the
   `images:` transformer to `kustomization.yml` pinned to that `sha-*` tag.
   Until that is done this overlay would pull the `:latest` placeholder, which
   is why the Application must not be synced first.
2. Create the `rbx-grant-reconciler` Secret in `rbx-identity`
   (`RBX_GRANT_RECONCILER_API_TOKEN`, `RBX_GRANT_RECONCILER_SIGNING_KEY`). The
   signing key only exists after step 4, so the first sync uses a placeholder
   and the Secret is updated once ZITADEL returns the real one.
3. Sync this Application. The pod starts in report-only mode
   (`RBX_GRANT_RECONCILER_APPLY=false`) and writes nothing.
4. Create the ZITADEL Actions v2 target pointing at
   `http://rbx-grant-reconciler.rbx-identity.svc.cluster.local/events/user-created`,
   then the execution on `user.human.added`. The target endpoint is the
   in-cluster Service, not a public host: there is no Ingress here on purpose.
5. Replace `REPLACE_WITH_PROJECT_ID` with the project that carries the baseline
   role, and confirm the role grants nothing in every product.
6. Read the logs. Only when the planned grants match what was intended, set
   `RBX_GRANT_RECONCILER_APPLY=true`.

## Rollback

Set `RBX_GRANT_RECONCILER_APPLY=false`. Grants already written stay and confer
nothing; removing them is not urgent. Deleting the ZITADEL execution stops
delivery.
