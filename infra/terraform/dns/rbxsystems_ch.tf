# Zone: rbxsystems.ch
# Primary nameserver: pantera (149.102.139.33) — ns1.rbxsystems.ch
# Secondary: eagle (167.86.92.97) — ns2.rbxsystems.ch

resource "powerdns_zone" "rbxsystems_ch" {
  name    = "rbxsystems.ch."
  kind    = "Master"
  account = ""

  # NS records are managed explicitly below for TTL control.
  nameservers = []
}

# --- NS + Glue ---

resource "powerdns_record" "rbxsystems_ch_ns" {
  zone    = powerdns_zone.rbxsystems_ch.name
  name    = "rbxsystems.ch."
  type    = "NS"
  ttl     = 86400
  records = ["ns1.rbxsystems.ch.", "ns2.rbxsystems.ch."]
}

resource "powerdns_record" "ns1_a" {
  zone    = powerdns_zone.rbxsystems_ch.name
  name    = "ns1.rbxsystems.ch."
  type    = "A"
  ttl     = 3600
  records = ["149.102.139.33"]
}

resource "powerdns_record" "ns1_aaaa" {
  zone    = powerdns_zone.rbxsystems_ch.name
  name    = "ns1.rbxsystems.ch."
  type    = "AAAA"
  ttl     = 3600
  records = ["2a02:c207:2256:6730::1"]
}

resource "powerdns_record" "ns2_a" {
  zone    = powerdns_zone.rbxsystems_ch.name
  name    = "ns2.rbxsystems.ch."
  type    = "A"
  ttl     = 3600
  records = ["167.86.92.97"]
}

resource "powerdns_record" "ns2_aaaa" {
  zone    = powerdns_zone.rbxsystems_ch.name
  name    = "ns2.rbxsystems.ch."
  type    = "AAAA"
  ttl     = 3600
  records = ["2a02:c207:2252:7581::1"]
}

# --- Web ---

resource "powerdns_record" "rbxsystems_ch_a" {
  zone    = powerdns_zone.rbxsystems_ch.name
  name    = "rbxsystems.ch."
  type    = "A"
  ttl     = 3600
  records = [var.k3s_ingress_ip]
}

resource "powerdns_record" "www_rbxsystems_ch" {
  zone    = powerdns_zone.rbxsystems_ch.name
  name    = "www.rbxsystems.ch."
  type    = "CNAME"
  ttl     = 3600
  records = ["rbxsystems.ch."]
}

# --- Email: root domain (rbxsystems.ch) ---

resource "powerdns_record" "rbxsystems_ch_mx" {
  zone    = powerdns_zone.rbxsystems_ch.name
  name    = "rbxsystems.ch."
  type    = "MX"
  ttl     = 3600
  records = ["10 inbound.postmarkapp.com."]
}

resource "powerdns_record" "rbxsystems_ch_spf" {
  zone    = powerdns_zone.rbxsystems_ch.name
  name    = "rbxsystems.ch."
  type    = "TXT"
  ttl     = 3600
  records = ["\"v=spf1 include:spf.mtasv.net ~all\""]
}

resource "powerdns_record" "rbxsystems_ch_dmarc" {
  zone    = powerdns_zone.rbxsystems_ch.name
  name    = "_dmarc.rbxsystems.ch."
  type    = "TXT"
  ttl     = 3600
  records = ["\"v=DMARC1; p=none; rua=mailto:dmarc@rbxsystems.ch; fo=1\""]
}

# DKIM — created only after Postmark provides the value
resource "powerdns_record" "rbxsystems_ch_dkim" {
  count = var.dkim_rbxsystems_ch != "" ? 1 : 0

  zone    = powerdns_zone.rbxsystems_ch.name
  name    = "pm._domainkey.rbxsystems.ch."
  type    = "CNAME"
  ttl     = 3600
  records = [var.dkim_rbxsystems_ch]
}

# --- Email: transactional subdomain (tx.rbxsystems.ch) ---
# Sender-only. MX handles Postmark bounce processing. No A record.

resource "powerdns_record" "tx_rbxsystems_ch_mx" {
  zone    = powerdns_zone.rbxsystems_ch.name
  name    = "tx.rbxsystems.ch."
  type    = "MX"
  ttl     = 3600
  records = ["10 inbound.postmarkapp.com."]
}

resource "powerdns_record" "tx_rbxsystems_ch_spf" {
  zone    = powerdns_zone.rbxsystems_ch.name
  name    = "tx.rbxsystems.ch."
  type    = "TXT"
  ttl     = 3600
  records = ["\"v=spf1 include:spf.mtasv.net ~all\""]
}

resource "powerdns_record" "tx_rbxsystems_ch_dmarc" {
  zone    = powerdns_zone.rbxsystems_ch.name
  name    = "_dmarc.tx.rbxsystems.ch."
  type    = "TXT"
  ttl     = 3600
  records = ["\"v=DMARC1; p=none; rua=mailto:dmarc@rbxsystems.ch; fo=1\""]
}

resource "powerdns_record" "tx_rbxsystems_ch_dkim" {
  count = var.dkim_tx_rbxsystems_ch != "" ? 1 : 0

  zone    = powerdns_zone.rbxsystems_ch.name
  name    = "pm._domainkey.tx.rbxsystems.ch."
  type    = "CNAME"
  ttl     = 3600
  records = [var.dkim_tx_rbxsystems_ch]
}

# --- DMARC cross-domain authorization ---
# Allows strategos.gr to send DMARC aggregate reports to dmarc@rbxsystems.ch.
# Required by RFC 7489 §7.1 when report destination is a different domain.

resource "powerdns_record" "dmarc_crossdomain_strategos" {
  zone    = powerdns_zone.rbxsystems_ch.name
  name    = "strategos.gr._report._dmarc.rbxsystems.ch."
  type    = "TXT"
  ttl     = 3600
  records = ["\"v=DMARC1\""]
}
