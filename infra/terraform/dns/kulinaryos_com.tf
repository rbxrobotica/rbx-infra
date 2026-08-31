# Zone: kulinaryos.com
# Owner: RBX Systems
# DNS authority migrated from Aruba/Technorail; mail remains hosted by Aruba.
# Source snapshot and migration runbook:
# docs/migrations/kulinaryos.com/README.md

resource "powerdns_zone" "kulinaryos_com" {
  name         = "kulinaryos.com."
  kind         = "Master"
  account      = ""
  soa_edit_api = "DEFAULT"

  nameservers = ["ns1.rbxsystems.ch.", "ns2.rbxsystems.ch."]
}

# --- NS ---

resource "powerdns_record" "kulinaryos_com_ns" {
  zone    = powerdns_zone.kulinaryos_com.name
  name    = "kulinaryos.com."
  type    = "NS"
  ttl     = 86400
  records = ["ns1.rbxsystems.ch.", "ns2.rbxsystems.ch."]
}

# --- Web and host addresses ---

resource "powerdns_record" "kulinaryos_com_a" {
  zone = powerdns_zone.kulinaryos_com.name
  name = "kulinaryos.com."
  type = "A"
  # RBX Traefik edge for the legacy WordPress origin at 78.47.113.97.
  ttl     = 300
  records = ["158.220.116.31"]
}

resource "powerdns_record" "app_kulinaryos_com_a" {
  zone    = powerdns_zone.kulinaryos_com.name
  name    = "app.kulinaryos.com."
  type    = "A"
  ttl     = 300
  records = ["116.203.21.141"]
}

resource "powerdns_record" "erp_kulinaryos_com_a" {
  zone    = powerdns_zone.kulinaryos_com.name
  name    = "erp.kulinaryos.com."
  type    = "A"
  ttl     = 300
  records = ["116.203.21.141"]
}

resource "powerdns_record" "gara_kulinaryos_com_a" {
  zone    = powerdns_zone.kulinaryos_com.name
  name    = "gara.kulinaryos.com."
  type    = "A"
  ttl     = 300
  records = ["116.203.21.141"]
}

resource "powerdns_record" "localhost_kulinaryos_com_a" {
  zone    = powerdns_zone.kulinaryos_com.name
  name    = "localhost.kulinaryos.com."
  type    = "A"
  ttl     = 3600
  records = ["127.0.0.1"]
}

resource "powerdns_record" "posta_kulinaryos_com_a" {
  zone = powerdns_zone.kulinaryos_com.name
  name = "posta.kulinaryos.com."
  type = "A"
  ttl  = 3600
  records = [
    "62.149.128.166",
    "62.149.128.151",
    "62.149.128.154",
    "62.149.128.157",
    "62.149.128.160",
    "62.149.128.74",
    "62.149.128.163",
  ]
}

resource "powerdns_record" "mx_kulinaryos_com_a" {
  zone = powerdns_zone.kulinaryos_com.name
  name = "mx.kulinaryos.com."
  type = "A"
  ttl  = 3600
  records = [
    "62.149.128.163",
    "62.149.128.151",
    "62.149.128.154",
    "62.149.128.157",
    "62.149.128.160",
    "62.149.128.166",
    "62.149.128.74",
  ]
}

resource "powerdns_record" "pop3_kulinaryos_com_a" {
  zone = powerdns_zone.kulinaryos_com.name
  name = "pop3.kulinaryos.com."
  type = "A"
  ttl  = 3600
  records = [
    "62.149.128.152",
    "62.149.128.158",
    "62.149.128.161",
    "62.149.128.164",
    "62.149.128.167",
    "62.149.128.73",
    "62.149.128.75",
    "62.149.128.155",
  ]
}

resource "powerdns_record" "smtp_kulinaryos_com_a" {
  zone = powerdns_zone.kulinaryos_com.name
  name = "smtp.kulinaryos.com."
  type = "A"
  ttl  = 3600
  records = [
    "62.149.128.203",
    "62.149.128.201",
    "62.149.128.202",
    "62.149.128.200",
  ]
}

resource "powerdns_record" "webmail_kulinaryos_com_a" {
  zone    = powerdns_zone.kulinaryos_com.name
  name    = "webmail.kulinaryos.com."
  type    = "A"
  ttl     = 3600
  records = ["62.149.158.91", "62.149.158.92"]
}

# --- Aliases ---

resource "powerdns_record" "domainconnect_kulinaryos_com_cname" {
  zone    = powerdns_zone.kulinaryos_com.name
  name    = "_domainconnect.kulinaryos.com."
  type    = "CNAME"
  ttl     = 3600
  records = ["_domainconnect.hst.aruba.it."]
}

resource "powerdns_record" "amministratore_kulinaryos_com_cname" {
  zone    = powerdns_zone.kulinaryos_com.name
  name    = "amministratore.kulinaryos.com."
  type    = "CNAME"
  ttl     = 3600
  records = ["admin.redirect.aruba.it."]
}

resource "powerdns_record" "autoconfigurazione_kulinaryos_com_cname" {
  zone    = powerdns_zone.kulinaryos_com.name
  name    = "autoconfigurazione.kulinaryos.com."
  type    = "CNAME"
  ttl     = 3600
  records = ["autodiscover.aruba.it."]
}

resource "powerdns_record" "ftp_kulinaryos_com_cname" {
  zone    = powerdns_zone.kulinaryos_com.name
  name    = "ftp.kulinaryos.com."
  type    = "CNAME"
  ttl     = 3600
  records = ["www.kulinaryos.com."]
}

resource "powerdns_record" "imap_kulinaryos_com_cname" {
  zone    = powerdns_zone.kulinaryos_com.name
  name    = "imap.kulinaryos.com."
  type    = "CNAME"
  ttl     = 3600
  records = ["imaps.aruba.it."]
}

resource "powerdns_record" "www_kulinaryos_com_cname" {
  zone    = powerdns_zone.kulinaryos_com.name
  name    = "www.kulinaryos.com."
  type    = "CNAME"
  ttl     = 3600
  records = ["kulinaryos.com."]
}

resource "powerdns_record" "ops_kulinaryos_com_cname" {
  zone    = powerdns_zone.kulinaryos_com.name
  name    = "ops.kulinaryos.com."
  type    = "CNAME"
  ttl     = 300
  records = ["custom-domains.chatgpt.site."]
}

# --- Kulinaryos Operations custom-domain validation ---

resource "powerdns_record" "openai_site_verification_ops_kulinaryos_com_txt" {
  zone    = powerdns_zone.kulinaryos_com.name
  name    = "_openai-site-verification.ops.kulinaryos.com."
  type    = "TXT"
  ttl     = 300
  records = ["\"openai-site-verification=W6QKnLbdQZAe5qBbpjwN0MYtaaqeqCbfqyOMLsVWiRs\""]
}

resource "powerdns_record" "cf_custom_hostname_ops_kulinaryos_com_txt" {
  zone    = powerdns_zone.kulinaryos_com.name
  name    = "_cf-custom-hostname.ops.kulinaryos.com."
  type    = "TXT"
  ttl     = 300
  records = ["\"25d13f10-68f8-4346-902f-d700f8a48502\""]
}

# --- Service discovery ---

resource "powerdns_record" "autodiscover_tcp_kulinaryos_com_srv" {
  zone    = powerdns_zone.kulinaryos_com.name
  name    = "_autodiscover._tcp.kulinaryos.com."
  type    = "SRV"
  ttl     = 3600
  records = ["0 0 443 autodiscover.aruba.it."]
}

resource "powerdns_record" "xmpp_client_tcp_kulinaryos_com_srv" {
  zone    = powerdns_zone.kulinaryos_com.name
  name    = "_xmpp-client._tcp.kulinaryos.com."
  type    = "SRV"
  ttl     = 3600
  records = ["5 0 5222 imchat1.aruba.it."]
}

resource "powerdns_record" "xmpp_server_tcp_kulinaryos_com_srv" {
  zone    = powerdns_zone.kulinaryos_com.name
  name    = "_xmpp-server._tcp.kulinaryos.com."
  type    = "SRV"
  ttl     = 3600
  records = ["5 0 5269 imchat1.aruba.it."]
}

# --- Aruba-hosted email ---

resource "powerdns_record" "kulinaryos_com_mx" {
  zone    = powerdns_zone.kulinaryos_com.name
  name    = "kulinaryos.com."
  type    = "MX"
  ttl     = 3600
  records = ["10 mx.kulinaryos.com."]
}

resource "powerdns_record" "kulinaryos_com_spf" {
  zone    = powerdns_zone.kulinaryos_com.name
  name    = "kulinaryos.com."
  type    = "TXT"
  ttl     = 3600
  records = ["\"v=spf1 include:_spf.aruba.it ~all\""]
}

resource "powerdns_record" "kulinaryos_com_dmarc" {
  zone    = powerdns_zone.kulinaryos_com.name
  name    = "_dmarc.kulinaryos.com."
  type    = "TXT"
  ttl     = 3600
  records = ["\"v=DMARC1; p=none; adkim=r; aspf=r;\""]
}

resource "powerdns_record" "kulinaryos_com_dkim" {
  zone = powerdns_zone.kulinaryos_com.name
  name = "a1._domainkey.kulinaryos.com."
  type = "TXT"
  ttl  = 3600
  records = [
    "\"v=DKIM1; h=sha256; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAoOhAWzBuKQYXU6E3E9efq+DPvtkkvWPg3EtB+BT2cTrMGh6Xy00mXfPi/EzubRpbhHpv3b3k1d65Vyhmpp5O3HzTQyzIqYMMF5iF5y05D5aEux3pj/i1g5LkAOFE0Xdf+wnI8zppP2jP7IV4bwKPr1OnBqOjs8hfBWSEGaCSFD/aVbOdNldHiwS\" \"alzDL38E+IcT0KQB+BdutC2T9B5idcSPaBY57sC4hq31wpBlgNcYpIMDxOQN+E9E+TbgnlJYCOYsD5S0amYFivcJObjSGpdstMBfaM1ox5iEQVpEhLz5cwgohjrMst2us7G9eHZ6p2jR7X+/yTP8ryAAywzMJ5wIDAQAB\"",
  ]
}
