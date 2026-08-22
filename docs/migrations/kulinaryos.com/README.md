# kulinaryos.com DNS authority migration

## Scope and ownership

- Domain: `kulinaryos.com`
- Owner: RBX Systems
- Purpose: production website and Aruba-hosted email
- Migration source: Aruba/Technorail authoritative DNS
- Authoritative destination: `ns1.rbxsystems.ch`, `ns2.rbxsystems.ch`
- Migration date: 2026-08-22
- Mail dependency: MX, SMTP, POP3, IMAP, webmail, SPF, DKIM and DMARC remain on Aruba

## Post-incident correction

The active public authority immediately before this migration was Cloudflare,
not the residual Aruba/Technorail zone. Restored project evidence identified
the former delegation as `daniella.ns.cloudflare.com` and
`ian.ns.cloudflare.com`. Both servers still held an authoritative zone during
incident recovery, including Cloudflare-proxied WordPress and direct records
for the legacy Vue/Laravel applications.

The Aruba panel capture was therefore not a complete backup of the active
public zone. In particular, apex `A 62.149.128.40` is an Aruba parking endpoint,
not the legacy website origin. The WordPress origin was subsequently identified
as the Plesk virtual host on `78.47.113.97`; the public application origin for
`app` and `erp` was `116.203.21.141`.

RBX authority is retained. The apex now targets an RBX Traefik proxy at
`158.220.116.31`, which terminates Let's Encrypt TLS and forwards the original
host to `78.47.113.97:443`. See
`apps/prod/kulinaryos-v1-proxy/README.md` for the live traffic path and
validation procedure.

This migration changes DNS authority only. It does not change the registrar,
application hosting, mailboxes or mail provider.

## Source capture

The versioned source backup is
`old-authoritative-2026-08-22.zone`. AXFR was denied, so individual RRsets were
queried directly from all legacy authoritative servers. Three reachable servers
agreed on every returned RRset; `dns2.technorail.com` refused IPv4 queries and
timed out over IPv6.

The `.com` parent had already been changed to the RBX nameservers when discovery
started. At that moment both RBX servers returned `REFUSED` for the absent zone,
and public resolvers returned `SERVFAIL`. This delegation change was not made by
the migration implementation.

The residual Aruba zone contained 33 general RDATA records plus one MX. The
operator-supplied pre-delegation panel capture contained nine additional records:

- seven `A` records for `posta.kulinaryos.com`;
- `amministratore.kulinaryos.com CNAME admin.redirect.aruba.it`;
- `autoconfigurazione.kulinaryos.com CNAME autodiscover.aruba.it`.

All reachable legacy authoritative servers returned `NXDOMAIN` for these names
after delegation. They are intentionally restored in the RBX zone from the
pre-delegation capture so the intended 42 general records are retained. This is
the only intentional data difference from the residual Aruba zone.

## Canonical implementation

The zone is declared in `infra/terraform/dns/kulinaryos_com.tf`. PowerDNS on
`pantera` is the primary, backed by PostgreSQL on `jaguar`; `eagle` is the
secondary and transfers zones with NOTIFY/AXFR into its BIND backend. The zone is
also listed in `bootstrap/ansible/group_vars/dns_servers.yml`, which renders the
secondary's `/etc/powerdns/named.conf`.

The RBX SOA defaults are used: `ns1.rbxsystems.ch`,
`hostmaster.rbxsystems.ch`, refresh 3600, retry 900, expire 604800 and negative
TTL 300. `SOA-EDIT-API=DEFAULT` supplies monotonically increasing date-based
serials. Record TTLs remain 3600 except the canonical RBX NS RRset TTL of 86400.

Discovery found PowerDNS Authoritative `4.8.3` running on both servers even
though the Ansible variable declares `4.9`. Public recursion is disabled, the
version string is anonymous, UDP/TCP 53 are open on IPv4/IPv6, primary AXFR is
restricted to the secondary, and the API listens only on pantera localhost.

## Deployment

1. Render the Ansible PowerDNS role for `eagle` so the secondary declares the
   new zone. Do not edit `/etc/powerdns/named.conf` permanently by hand.
2. Open the SSH tunnel to the primary API.
3. Run the repository's `scripts/dns-tofu-env.sh` wrapper around `tofu plan` and
   `tofu apply` from `infra/terraform/dns`.
4. Confirm the primary SOA and force `pdns_control retrieve kulinaryos.com` on
   `eagle` only if NOTIFY/AXFR has not converged within one minute.
5. Run the complete validation below against both servers.

The 2026-08-22 apply was deliberately isolated to this zone because no historic
local OpenTofu state for the other four zones was present on the workstation or
DNS hosts. The saved plan contained `22 to add, 0 to change, 0 to destroy`; the
apply completed with the same totals, and the post-apply plan reported no
changes. A local ignored state copy is retained as
`infra/terraform/dns/kulinaryos.tfstate`; it is not a replacement for importing
the zone into the operator's authoritative full-root state if that state exists
elsewhere.

During the apply, concurrent creation of three SRV records produced one duplicate
PowerDNS empty non-terminal row for `_tcp`. The duplicate internal row was
identified by exact database ID, removed transactionally, and the remaining
canonical ENT was retained. No public RRset changed. Final `pdnsutil check-zone`
result: 48 records, 0 errors, 0 warnings.

## Validation

For each server in `ns1.rbxsystems.ch ns2.rbxsystems.ch`, query:

```bash
dig @SERVER kulinaryos.com A
dig @SERVER kulinaryos.com NS
dig @SERVER kulinaryos.com SOA
dig @SERVER kulinaryos.com MX
dig @SERVER mx.kulinaryos.com A
dig @SERVER kulinaryos.com TXT
dig @SERVER _dmarc.kulinaryos.com TXT
dig @SERVER a1._domainkey.kulinaryos.com TXT
dig @SERVER www.kulinaryos.com CNAME
dig @SERVER ftp.kulinaryos.com CNAME
dig @SERVER imap.kulinaryos.com CNAME
dig @SERVER _autodiscover._tcp.kulinaryos.com SRV
dig @SERVER _xmpp-client._tcp.kulinaryos.com SRV
dig @SERVER _xmpp-server._tcp.kulinaryos.com SRV
```

Also verify UDP and TCP responses, matching SOA serials, the authoritative flag,
public recursion refusal, and the Aruba mail hosts `posta`, `pop3`, `smtp`,
`webmail` and `autoconfigurazione`.

Final result on 2026-08-22:

- SOA serial `2026082208` matched on ns1 and ns2;
- all 22 served RRsets matched between ns1 and ns2;
- all 17 RRsets still present on Aruba matched ns1 after ignoring record order;
- IPv4 and IPv6, UDP and TCP returned the same authoritative SOA on both hosts;
- public AXFR failed on both hosts and public recursion returned `REFUSED`;
- Cloudflare, Google and Quad9 returned the expected apex, MX, SPF, DKIM and
  DMARC data;
- a root-to-authority trace completed through `.com` to ns2 without lame or
  `SERVFAIL` responses.

## DNSSEC

`DNSSEC parent DS: absent` at the time of migration. No DNSSEC material is
introduced in this equivalence migration.

## Rollback

Do not use `62.149.128.40` as a website rollback target; it serves Aruba's
parking page. The actual pre-cutover public authority was Cloudflare at
`daniella.ns.cloudflare.com` and `ian.ns.cloudflare.com`. Re-delegating the
parent to those servers is an emergency authority rollback only if the
Cloudflare account and zone ownership have first been verified.

For a proxy-only rollback while retaining RBX authority, restore the previous
apex RRset from the last known-good OpenTofu state and remove the
`kulinaryos-v1-proxy` resources only after confirming another valid website
path. Never remove the live proxy while the apex still resolves to the RBX
Traefik edge.

To roll back an unmerged RBX implementation only, revert the feature commit and
apply the canonical OpenTofu/Ansible workflow. Do not remove the live RBX zone
while the parent still delegates to RBX.

## Post-cutover observation

```bash
dig NS kulinaryos.com
dig +trace kulinaryos.com
dig SOA kulinaryos.com
dig MX kulinaryos.com
dig @1.1.1.1 kulinaryos.com
dig @8.8.8.8 kulinaryos.com
dig @9.9.9.9 kulinaryos.com
```

Watch the apex, `www`, mail-related hosts, MX, SPF, DKIM and DMARC for unexpected
`SERVFAIL` or `NXDOMAIN`. Parent delegation has a 172800-second TTL, so caches
that observed the old delegation can coexist for up to 48 hours.

## Known operational risks

- No automated `pg_dump`/backup job for the `pdns` database was found in the
  inspected repository, systemd timers or cron configuration on `jaguar`.
- No dedicated PowerDNS Prometheus exporter or external DNS blackbox probe was
  found; current observability is daemon health plus journald and manual queries.
- The running PowerDNS version (`4.8.3`) differs from the declared Ansible
  version (`4.9`). This migration does not upgrade the daemon.
- The repository uses ignored local OpenTofu state, but the historic full-root
  state was unavailable. Import this zone into that state before any future
  full-root apply from another operator workstation.
