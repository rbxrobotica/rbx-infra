# POCO private IRC cutover candidate

Status: prepared, not deployed, October 17, 2026. The POCO is enrolled; its
existing grant permits only the Corbetti TCP/18080 probe. No DNS, certificate,
policy, account or service change is performed by these artifacts.

## Concrete candidate

- DNS: `irc.rbxsystems.ch A 100.64.0.2`, TTL 300. No AAAA for this increment.
  Review-only Terraform is in `infra/irc/ergo/mobile-dns.tf.example`; it is not
  loaded by the managed DNS root.
- Transport: add `100.64.0.2:6697:6697/tcp` with the opt-in
  `infra/irc/ergo/compose.mesh-pilot.yaml`. Preserve both loopback bindings and
  `ERGO_TLS_BIND_ADDRESS=127.0.0.1`. Do not deploy with the legacy bootstrap,
  which knows only the base Compose file and could remove the extra binding.
- Certificate: trusted public chain for exactly the client name above, installed
  at the existing Ergo TLS paths. Do not rename the IRC server identity merely
  to change its connection hostname. Audit any existing TLS consumers before
  replacing the self-signed chain; plaintext-over-SSH desktop transport remains.
- Add exactly this Headscale grant to the fresh existing policy:

  ```json
  {"src":["poco-rbx-trusted@"],"dst":["corbetti-service@"],"ip":["tcp:6697"]}
  ```

  Preserve both existing grants, including TCP/18080. Run Headscale native tests
  for allow 6697 and deny 22/6667/database/other administrative destinations.
  The earlier candidate runner tests the unchanged adoption policy; its previous
  success does not validate this new permission.
- Application: a separate proposed `robson-poco` IRC account, with no OPER or
  founder rights. Confirm available account name and owner mapping before
  creation. Explicitly approve channels and history access; do not copy desktop
  account privileges. Generate and store its credential in `pass`, never Git,
  IRC, logs or a command argument. Select the mobile client only after validating
  its certificate and credential storage behavior.

Compose retains distinct port mappings by host IP. The additional file is
deliberately not named `compose.override.yaml`, preventing implicit activation.
[Docker Compose merge specification](https://docs.docker.com/reference/compose-file/merge/).

## Certificate renewal is a deployment prerequisite

The inspected Corbetti command path had neither `lego` nor `certbot`, and the
listed timers contained no ACME/certificate timer. This does not rule out other
issuers or automation elsewhere. The repository's shared cert-manager issuer
uses HTTP-01 and is not a ready DNS-01 solution for this private endpoint.

Prefer a small DNS-01 issuer with a pinned, verified binary. Lego supports
PowerDNS and file-based credential inputs, but this does not establish that the
current PowerDNS API key is narrowly scoped. Do not copy a broad authoritative
DNS API key to Corbetti to unblock this pilot. Before implementation, determine
whether an existing issuer can receive a delegated `_acme-challenge` zone or an
endpoint that restricts writes to the required challenge. Confirm authoritative
propagation, credential custody and renewal failure behavior.
[Lego PowerDNS provider](https://go-acme.github.io/lego/dns/pdns/).

Proposed responsibility: RBX infrastructure operator owns issuance and recovery;
a systemd timer on the selected issuer renews, verifies name/chain/key matching,
and installs a complete certificate pair atomically before the documented Ergo
reload. Verify that reload behavior against the installed Ergo version before
automating it. Renew well before expiry; check the deployed certificate and
alert at 30/14/7 days (adjust to the selected certificate lifetime). A successful
ACME command alone is not a successful deployment. Issuer placement and scoped
challenge access remain open; no issuer was installed in this increment.

## Read-only preflight

`scripts/network/irc_mobile_preflight.py` checks the resolved Compose port
bindings, an exact additive JSON policy delta and a staged public certificate.
It does not read a private key or change any service. Use private files for all
resolved configuration output, since Compose may include environment secrets.

```bash
python3 scripts/network/irc_mobile_preflight.py \
  --compose /private/pilot/compose-resolved.json \
  --policy-before /private/pilot/policy-before.json \
  --policy-after /private/pilot/policy-after.json \
  --fullchain /private/pilot/fullchain.pem \
  --leaf /private/pilot/leaf.pem \
  --intermediates /private/pilot/intermediates.pem \
  --ca-bundle /etc/ssl/certs/ca-certificates.crt
```

Resolve the exact deployed base, environment and pilot override with native
Docker Compose before feeding the JSON to this check. Do not substitute synthetic
defaults for that live preflight. Normalize HuJSON with a proper parser if the
policy contains comments; the checker intentionally accepts strict JSON only.
The certificate check requires at least 30 days of remaining validity, a matching
hostname and trusted chain. It cannot establish private-key matching, DNS
resolution, listener reachability or SASL/channel permissions. It is a bounded
preflight, not a complete audit of arbitrary Compose files or Headscale policies.

## Authorization and recovery gates

Before requesting production approval, resolve the issuer prerequisite and
prepare exact diffs against fresh sources, private backups and native checks.
The approval must name the DNS record, certificate installation/reload, Compose
recreation, additive grant and account/channel changes. A generic request to
continue preparation is not approval for these production operations.

Take a consistent Ergo configuration/database backup and retain the existing
certificate pair with restricted permissions. Record Compose/environment and
Headscale policy hashes. Keep a verified administrative SSH session outside the
new phone path. Apply one component at a time and stop on unexpected results.

Accept only when the phone verifies normal TLS trust and the correct name,
authenticates with its own account, reaches authorized channels and is refused
unauthorized channels. Recheck desktop IRC. Test Wi-Fi/mobile transitions and
screen lock separately. Confirm public IRC ports remain unreachable from an
authorized external probe; private binding alone is not the full firewall test.

Rollback removes only the new grant and mesh binding, revokes the new IRC
credential, and restores the previous valid certificate pair if replacement
caused regression. Restore the original Compose invocation and confirm both
desktop loopback bindings. Restore DNS to its reviewed prior state rather than
blindly deleting a potentially pre-existing record. Do not delete the phone's
mesh enrollment as an incidental IRC rollback.

## Validation performed

Six local tests pass, including real ephemeral OpenSSL certificate chains with
wrong-hostname, untrusted-chain and short-validity rejection. The installed
Corbetti Compose parser accepted an isolated repository model with the extra
binding and without the production environment file. This did not validate a
native two-file merge with the production environment: that file was unreadable
to the existing account and passwordless sudo was unavailable. No bypass, live
apply, account creation, certificate issuance or DNS write was attempted.
