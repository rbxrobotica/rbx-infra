# Robson RBX Identity cutover

**Date:** 2026-11-18  
**Status:** Prepared; live registration and rollout remain human-gated

## Target contract

Robson accepts only ZITADEL access tokens issued by
`https://auth.merovelis.com`. Google is the sole upstream human identity
provider for the selected organization; neither Google tokens, email
allowlists nor static API tokens reach `robsond`.

Production and testnet have independent API projects and public clients. Each
environment maps one ZITADEL organization to exactly one existing Robson
projection tenant. Never generate a replacement tenant during this cutover.

## ZITADEL objects

Create the objects first and record their real numeric IDs. Do not put client
secrets in a Tauri, browser or CLI build.

1. Create a production Robson API project and a separate testnet project.
2. In each project, define these exact roles:
   - `rbx:robson:observer`
   - `rbx:robson:operator`
   - `rbx:robson:funding`
   - `rbx:robson:emergency`
3. Create public clients for web/Tauri and CLI. Tauri uses Authorization Code
   with PKCE (`S256`), native redirect URIs and no client secret. CLI uses the
   Device Authorization Grant and no client secret.
4. Configure JWT access tokens and enable project-role assertion in access
   tokens (`accessTokenRoleAssertion=true`) for every client accepted by
   `robsond`.
5. Grant only the necessary roles to the owner's canonical ZITADEL subject.
   Additional users require explicit grants and an intentional addition to the
   allowed-subject list.
6. On the tenant organization login policy, disable local username/password,
   enable external IdP login, and attach only the approved Google IdP. Keep
   automatic account creation/linking consistent with the RBX Identity policy.

Client authorization requests must contain `openid`, the organization selector
`urn:zitadel:iam:org:id:<organization-id>`, and the API audience selector
`urn:zitadel:iam:org:project:id:<project-id>:aud`. `offline_access` is required
where refresh is supported. The optional direct IdP selector is
`urn:zitadel:iam:org:idp:id:<google-idp-id>`. A project roles claim name is not
an authorization-request scope.

## Durable metadata

After provisioning, store only the returned identifiers in `pass`:

```text
rbx/robson/oidc-audience
rbx/robson/oidc-allowed-client-ids
rbx/robson/oidc-organization-id
rbx/robson/oidc-allowed-subjects

rbx/robson-testnet/oidc-audience
rbx/robson-testnet/oidc-allowed-client-ids
rbx/robson-testnet/oidc-organization-id
rbx/robson-testnet/oidc-allowed-subjects
```

Audience, organization and subject entries are numeric ZITADEL IDs. Allowed
client IDs and subjects are comma-separated numeric IDs without spaces. The
Ansible role combines the organization ID with the existing projection tenant
as `<organization-id>=<tenant-uuid>` and removes the obsolete `api-token` key
from the managed Kubernetes Secret.

## Preflight and sequence

1. Verify the existing production and testnet projection tenant UUIDs are
   backed up and differ.
2. Verify a real login for every registered client. Inspect the access token
   locally and confirm exact issuer, API audience, client ID, canonical
   subject, organization-scoped role claim and short expiry. Never paste the
   token into an issue or log.
3. Run the manifest contracts locally:

   ```bash
   python3 scripts/validate-robson-prod-secret-scope.py
   python3 scripts/validate-robson-rbx-identity.py
   kubectl kustomize apps/prod/robson >/dev/null
   kubectl kustomize apps/testnet/robson >/dev/null
   ```

4. Reconcile the role-managed Secrets only after all pass entries exist. This
   changes configuration but does not restart pods:

   ```bash
   ansible-playbook bootstrap/ansible/bootstrap-k8s-secrets-only.yml \
     -i bootstrap/ansible/inventory/hosts.yml \
     --tags robson-prod-secret
   ```

5. Promote testnet first with reviewed immutable application images. Validate
   401 without a token, 403 with the wrong role, successful observer reads,
   each mutation role, tenant returned by `/auth/session`, refresh, logout and
   Google-only login.
6. Promote production in one reviewed window: compatible backend image, web
   client image and these manifests. ArgoCD promotion remains a separate human
   approval; this repository change does not perform it.

## Rollback

The new backend deliberately rejects old auth variables. Roll back the
application image and its matching configuration together; do not restore the
static API token as a compatibility bridge. Preserve the same projection tenant
and exchange credentials. If identity validation is uncertain, keep the API
unavailable while the daemon's internal safety loop remains running, then fix
the registration rather than weakening verification.
