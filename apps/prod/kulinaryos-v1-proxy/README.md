# Kulinaryos v1 WordPress proxy

This overlay keeps `kulinaryos.com` authoritative on the RBX PowerDNS servers
while Traefik terminates public TLS and proxies the legacy institutional
WordPress site.

## Traffic path

```text
kulinaryos.com / www.kulinaryos.com
  -> 158.220.116.31 (RBX Traefik)
  -> 78.47.113.97:443 (legacy Plesk WordPress origin)
```

The origin selects the WordPress virtual host from the original `Host` header.
It currently presents the self-signed Plesk panel certificate, so the scoped
`ServersTransport` skips origin certificate verification. Client-facing TLS is
issued by `letsencrypt-prod` and stored in `kulinaryos-com-tls`.

This is a recovery bridge. Replace `insecureSkipVerify` when a publicly trusted
origin certificate is installed, or remove the proxy after WordPress is moved
to infrastructure owned by Kulinaryos.

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
