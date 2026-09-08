# RBX Market Graph GitOps Activation Gate

This directory is an inert production manifest candidate. It is intentionally
absent from `gitops/app-of-apps`, the `rbx-applications` project allowlist and
the cross-namespace ExternalSecret RBAC. Merging this directory alone cannot
create or sync an ArgoCD Application.

Activation requires all of the following:

1. Ratify Governance ADR-0609 through ADR-0613.
2. Merge the reviewed product implementation.
3. Publish both GHCR images for the same reviewed source SHA and verify their
   manifests before changing the kustomization pins.
4. Choose and govern the product hostname, then add DNS, Certificate,
   IngressRoute, `ORIGIN` and the exact OIDC redirect URI.
5. Register the rbx-identity web client, API audience and four product scopes.
6. Provision the Jaguar database with a schema-owner migration role and the
   non-owner `rbx_market_graph_app` role. Both URLs must use the
   `rbx-market-graph-postgres` service and `search_path=public`.
7. Add Jaguar `pg_hba.conf` entries for the applicable pod subnet and verify a
   backup before reload under the external PostgreSQL runbook.
8. Create the source Secret `rbx-ia-br/rbx-market-graph-secrets`, the namespace
   GHCR pull Secret, and the narrow source-secret Role and RoleBinding.
9. Independently review tenant RLS, personal-data access audit, retention and
   observability behavior.
10. Only under a separate operator deployment authorization, add the AppProject
    destination and `gitops/app-of-apps/rbx-market-graph.yml`.

No step in this branch creates credentials, DNS, a Jaguar role or database,
publishes an image, registers ArgoCD, syncs a cluster or enables an external
integration.
