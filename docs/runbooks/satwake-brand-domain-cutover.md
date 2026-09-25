# Satwake brand-domain cutover (prepared, not executed)

The public domain is `satwake.com`. This runbook is a reviewable preparation
for an operator-approved production change. The campaign
`ritual.satwake.com` remains a draft and has no Ingress or DNS record here.
The existing `briefingbtc.merovelis.com` Ingress and product identifiers stay
in place during this cutover.

## Observed state on 2026-09-25

- Public `satwake.com` NS: `launch1.spaceship.net` and `launch2.spaceship.net`.
  Its A records returned `34.216.117.25` and `54.149.79.189`.
- `www.satwake.com` and `ritual.satwake.com` returned no A/CNAME record.
- The existing landing host `briefingbtc.merovelis.com` resolved to
  `158.220.116.31` (the current cluster ingress address). Recheck all of
  these immediately before approval; DNS observations can change.
- The infrastructure Terraform PowerDNS tree does not control this domain
  while its authoritative nameservers are Spaceship. A GitOps PR alone cannot
  perform the DNS change.

## Proposed controlled sequence

1. Review the Satwake landing image from the landing repository and promote
   it while preserving the legacy host. The current Kustomization pins an
   older Briefing BTC image; adding the Ingress by itself would serve that
   older image on the new domain.
2. With exact approval for the production GitOps sync, apply the separate
   Satwake Ingress for `satwake.com` and `www.satwake.com`. It requests its own
   certificate from the existing `letsencrypt-prod` HTTP-01 issuer. It does
   not replace the legacy host's certificate.
3. With exact DNS approval in the authoritative Spaceship zone, replace the
   apex A records with the current cluster ingress IP (observed as
   `158.220.116.31`) and create `www` as a CNAME to `satwake.com`. Coordinate
   this with certificate issuance: HTTP-01 cannot complete until the names
   resolve to the Ingress. Plan for propagation and temporary TLS failure;
   do not present the host as live before the certificate is ready.
4. Verify authoritative and recursive DNS, certificate SANs, HTTPS on the
   apex, the `www` redirect to the apex, static assets, health endpoint,
   checkout CORS/status polling, and continued legacy-host behavior. Verify
   that Plausible receives an event for the actual apex host. Record the
   observations before calling the domain operational.

The campaign host requires a separately approved campaign record, exact
Ingress/TLS/DNS entry, Commerce CORS entry and matching landing build.
Unknown public subdomains cannot be promised a redirect until DNS and TLS
are arranged for them; the application's Nginx default route only handles
requests that reach the service.
