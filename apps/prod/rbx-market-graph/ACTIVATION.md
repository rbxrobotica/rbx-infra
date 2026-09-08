# RBX Market Graph Production Activation

Owner authorization was recorded on 2026-09-08. The canonical operator host is
`https://kairos.rbxsystems.ch`; the API remains cluster-internal.

## Gate record

1. **Ratification — complete.** The owner accepted ADR-0609 through ADR-0613;
   governance PR #78 records `active/Accepted` in the canonical files.
2. **Product — merged.** Product PR #1 is on `main`.
3. **Release — complete.** Product PR #2 added the required ZITADEL claim
   adapter. Both API and web images for main SHA `9479b8ee78f896e2c71c54b547a909afec36930c`
   were published and the overlay pins that identical immutable SHA.
4. **Hostname — complete.** Certificate, HTTPS route, `ORIGIN`, exact redirect
   URI and the `kairos.rbxsystems.ch` PowerDNS resource are declared. The lost
   local OpenTofu state was reconstructed by importing 111 live resources; the
   targeted plan then created only the Kairos A record, with zero changes or
   destroys. Both authoritative nameservers now serve `158.220.116.31`.
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
10. **GitOps registration — ready.** The ArgoCD Application is the final
    activation switch in this PR. Automated sync starts only after owner merge.

## Rollback

Remove the ArgoCD Application first, preserving the namespace and database for
forensics. Revert the DNS resource through the canonical OpenTofu state. Revoke
the ZITADEL client or owner grant to stop login immediately. Roll an application
release back by restoring both image pins to the same prior verified SHA. Do not
drop the database or Claims as part of an application rollback.
