# RBX Market Graph Production Activation

Owner authorization was recorded on 2026-09-08. The canonical operator host is
`https://kairos.rbxsystems.ch`; the API remains cluster-internal.

## Gate record

1. **Ratification — awaiting merge.** The owner accepted ADR-0609 through
   ADR-0613; governance PR #78 records `active/Accepted` in the canonical files.
2. **Product — merged.** Product PR #1 is on `main`.
3. **Release — awaiting production auth build.** The initial source-SHA images
   were published and verified. Product PR #2 adds the required ZITADEL claim
   adapter and fleet-standard atomic image promotion. Its resulting main SHA,
   not the initial image, is the eligible production pin.
4. **Hostname — configured, DNS apply pending.** Certificate, HTTPS route,
   `ORIGIN`, exact redirect URI and the `kairos.rbxsystems.ch` PowerDNS resource
   are declared. Apply DNS only from the canonical OpenTofu state.
5. **Identity — complete.** The dedicated ZITADEL project, confidential web
   client, JWT project audience, four tenant-bound roles and owner grant exist.
6. **Jaguar — complete.** Database `rbx_market_graph` has separate owner/migration
   and non-owner application roles. Application URLs use the selectorless
   `rbx-market-graph-postgres` Service and `search_path=public`.
7. **Database network and backup — complete.** Node-scoped SCRAM entries were
   added only after a PostgreSQL globals dump and `pg_hba.conf` copy were
   verified; PostgreSQL reloaded successfully.
8. **Secrets — staged.** Source and GHCR pull Secrets exist. This change adds the
   exact cross-namespace Role and RoleBinding needed by External Secrets.
9. **Security review — complete for activation.** Tenant RLS is forced, Claims
   require evidence, sensitive reads are audited, telemetry excludes evidence
   bodies and external-effect integrations remain disabled.
10. **GitOps registration — authorized, intentionally last.** Add the ArgoCD
    Application only after PR #2 is merged, both promoted manifests exist and
    the kustomization contains that same main SHA.

## Rollback

Remove the ArgoCD Application first, preserving the namespace and database for
forensics. Revert the DNS resource through the canonical OpenTofu state. Revoke
the ZITADEL client or owner grant to stop login immediately. Roll an application
release back by restoring both image pins to the same prior verified SHA. Do not
drop the database or Claims as part of an application rollback.
