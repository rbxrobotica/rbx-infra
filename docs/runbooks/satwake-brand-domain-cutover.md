# Satwake brand-domain cutover (prepared, not executed)

The public domain is `satwake.com`. This runbook is a reviewable preparation
for an operator-approved production change. The campaign
`ritual.satwake.com` remains a draft and has no Ingress or DNS record here.
The existing `briefingbtc.merovelis.com` Ingress and product identifiers stay
in place during this cutover.

## Observed state on 2026-09-26

- Public `satwake.com` NS: `launch1.spaceship.net` and `launch2.spaceship.net`.
  An authoritative query to `launch1.spaceship.net` returned apex A records
  `34.216.117.25` and `54.149.79.189`. The domain owner reports making no DNS
  configuration since purchase; these records are not evidence of an existing
  Satwake site. Inspect the Spaceship zone before replacing them.
- `www.satwake.com` and `ritual.satwake.com` returned no A/CNAME record.
- The existing landing host `briefingbtc.merovelis.com` resolved to
  `158.220.116.31` and returned HTTPS 200. The current landing Ingress reports
  multiple node addresses; this is the address used by the working legacy host.
  Recheck all of these immediately before approval; DNS observations can change.
- The infrastructure Terraform PowerDNS tree does not control this domain
  while its authoritative nameservers are Spaceship. A GitOps PR alone cannot
  perform the DNS change.

## Proposed controlled sequence

1. Review the Satwake landing image from the landing repository. Landing PRs
   #2 through #5 merged on 2026-09-26. A `satwake.com` Plausible site and its
   dedicated build variable are configured; the analytics check and the
   [main image build](https://github.com/rbxrobotica/rbx-landing-briefing-btc/actions/runs/36219779196)
   passed. Infra PR #299 prepares the image tag `sha-a2466c2` pinned to digest
   `sha256:b11f78506b8a9b700329d4af1b76f9ead392c265f4b2521fa77eda607be9a95b`.
   The tag was reused by the build workflow, so the digest identifies the
   analytics-enabled artifact. Its email-verification UI still needs the
   Commerce-to-Comms release gates before deployment. The current production
   Kustomization pins the older Briefing BTC image `sha-8a02318`; adding the
   Ingress by itself would serve that image on the new domain. Promote #299
   under its own deployment approval before syncing this Ingress. Rebase the
   second PR merged so the Kustomization retains both this Ingress and the
   exact image digest. Preserve the legacy host during the transition.
2. With exact approval for the production GitOps sync, apply the separate
   Satwake Ingress for `satwake.com` and `www.satwake.com`. It requests its own
   certificate from the existing `letsencrypt-prod` HTTP-01 issuer. It does
   not replace the legacy host's certificate.
3. With exact DNS approval in the authoritative Spaceship zone, inspect the
   zone for any unreported records or services, then replace the two observed
   apex A records with one A record for the verified working ingress IP
   (currently `158.220.116.31`) and create `www` as a CNAME to `satwake.com`.
   Coordinate this with certificate issuance: HTTP-01 cannot complete until
   the names resolve to the Ingress. Plan for propagation and temporary TLS failure;
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
