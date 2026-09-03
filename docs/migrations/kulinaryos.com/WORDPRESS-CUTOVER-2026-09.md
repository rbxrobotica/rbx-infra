# WordPress cutover to Food Process DigitalOcean

This change is the final traffic switch for the original Kulinaryos WordPress
site. It changes only the apex `A` record from the RBX Traefik proxy
(`158.220.116.31`) to the Food Process DigitalOcean Traefik edge
(`157.230.125.23`). `www.kulinaryos.com` remains a CNAME to the apex. Mail,
`app`, `erp`, `social`, `test`, operations, and verification records are not
changed.

## Hard prerequisites

Do not apply or merge this cutover until all of the following are recorded in
the change ticket:

1. `Food-Process-Limited/kulinaryos-institutional-site#1` is merged and its
   immutable image exists in GHCR with the reviewed digest.
2. The external MariaDB 10.11.14 database is restored, backed up, and its fresh
   backup has passed a restore test.
3. `Food-Process-Limited/food-process-infra#10` is merged, ArgoCD reports the
   WordPress application Synced/Healthy, and the upload PVC is Bound.
4. Direct-origin tests against `157.230.125.23` pass for the homepage,
   navigation, WPML routes, Elementor assets, uploads, login and 404 handling.
5. The certificate for both `kulinaryos.com` and `www.kulinaryos.com` is Ready.
6. The legacy proxy at `158.220.116.31` and origin at `78.47.113.97` remain
   intact and recoverable for the rollback window.
7. An operator has explicitly authorized the exact OpenTofu apply.

## Plan and apply boundary

Use the repository DNS wrapper and the canonical state described in the parent
runbook. The saved plan must contain exactly one in-place record update:

```text
powerdns_record.kulinaryos_com_a: records 158.220.116.31 -> 157.230.125.23
Plan: 0 to add, 1 to change, 0 to destroy
```

Any other change aborts the cutover. Applying the plan, forcing a secondary
transfer, and deleting the old proxy are separate production operations and
require their own authorization.

## Validation

Query both authoritative servers and at least Cloudflare, Google and Quad9.
Confirm the new apex, unchanged `www` CNAME, matching SOA serials, valid HTTPS,
HTTP-to-HTTPS redirect, `www`-to-apex redirect, original WordPress rendering,
uploads and database-backed routes. Recheck all mail records to prove that the
cutover did not affect mail.

## Rollback

For a site-impacting failure, restore the apex record to `158.220.116.31`
through the same reviewed OpenTofu workflow. Do not change the nameserver
delegation and do not delete the Food Process database/PVC. Keep the old RBX
proxy and Hetzner origin available for at least seven days after successful
cutover, then retire them only under a separate approved change.
