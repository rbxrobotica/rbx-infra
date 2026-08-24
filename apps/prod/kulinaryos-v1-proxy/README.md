# Kulinaryos institutional site recovery

This overlay keeps `kulinaryos.com` authoritative on the RBX PowerDNS servers,
terminates public TLS at Traefik, and serves the institutional site from the
RBX cluster. It no longer depends on the legacy Plesk WordPress origin.

## Traffic path

```text
kulinaryos.com / www.kulinaryos.com
  -> 158.220.116.31 (RBX Traefik)
  -> kulinaryos-institutional (namespace: kulinaryos)
  -> immutable GHCR image built from rbxrobotica/kulinaryos
```

Client-facing TLS is issued by `letsencrypt-prod` and stored in
`kulinaryos-com-tls`. `www` permanently redirects to the apex. The deployment
shares the existing `kulinaryos` namespace so it can reuse the managed
`ghcr-pull-secret`; `erp.kulinaryos.com` is outside this overlay and remains
unchanged.

The image build first attempts a bounded static capture of the legacy origin
from GitHub Actions. If the origin is blocked or unhealthy, it uses the
versioned institutional fallback from `apps/institutional/site`.

## Validation

```bash
dig @149.102.139.33 kulinaryos.com A
dig @167.86.92.97 kulinaryos.com A
curl -I http://kulinaryos.com
curl -I https://kulinaryos.com
curl -I https://www.kulinaryos.com
```

Expected results: apex `A 158.220.116.31`, HTTP redirects to HTTPS, apex HTTPS
returns `200`, and `www` redirects to `https://kulinaryos.com/`.
