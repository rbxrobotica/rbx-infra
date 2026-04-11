# Zone: strategos.gr
# Delegated to ns1/ns2.rbxsystems.ch — glue records live in rbxsystems.ch zone.

resource "powerdns_zone" "strategos_gr" {
  name    = "strategos.gr."
  kind    = "Master"
  account = ""

  # nameservers must match what PowerDNS has in state (set at zone creation).
  # Actual NS records with TTL control are managed via powerdns_record below.
  # Changing this attribute forces zone replacement — do not remove or reorder.
  nameservers = ["ns1.rbxsystems.ch.", "ns2.rbxsystems.ch."]
}

# --- NS ---

resource "powerdns_record" "strategos_gr_ns" {
  zone    = powerdns_zone.strategos_gr.name
  name    = "strategos.gr."
  type    = "NS"
  ttl     = 86400
  records = ["ns1.rbxsystems.ch.", "ns2.rbxsystems.ch."]
}

# --- Web ---

resource "powerdns_record" "strategos_gr_a" {
  zone    = powerdns_zone.strategos_gr.name
  name    = "strategos.gr."
  type    = "A"
  ttl     = 3600
  records = [var.k3s_ingress_ip]
}

resource "powerdns_record" "www_strategos_gr" {
  zone    = powerdns_zone.strategos_gr.name
  name    = "www.strategos.gr."
  type    = "CNAME"
  ttl     = 3600
  records = ["strategos.gr."]
}

# --- Email: root domain (strategos.gr) ---

resource "powerdns_record" "strategos_gr_mx" {
  zone    = powerdns_zone.strategos_gr.name
  name    = "strategos.gr."
  type    = "MX"
  ttl     = 3600
  records = ["10 inbound.postmarkapp.com."]
}

resource "powerdns_record" "strategos_gr_spf" {
  zone    = powerdns_zone.strategos_gr.name
  name    = "strategos.gr."
  type    = "TXT"
  ttl     = 3600
  records = ["\"v=spf1 include:spf.mtasv.net ~all\""]
}

resource "powerdns_record" "strategos_gr_dmarc" {
  zone    = powerdns_zone.strategos_gr.name
  name    = "_dmarc.strategos.gr."
  type    = "TXT"
  ttl     = 3600
  records = ["\"v=DMARC1; p=none; rua=mailto:dmarc@rbxsystems.ch; fo=1\""]
}

resource "powerdns_record" "strategos_gr_dkim" {
  count = var.dkim_strategos_gr != "" ? 1 : 0

  zone    = powerdns_zone.strategos_gr.name
  name    = "pm._domainkey.strategos.gr."
  type    = "CNAME"
  ttl     = 3600
  records = [var.dkim_strategos_gr]
}

# --- Email: transactional subdomain (tx.strategos.gr) ---

resource "powerdns_record" "tx_strategos_gr_mx" {
  zone    = powerdns_zone.strategos_gr.name
  name    = "tx.strategos.gr."
  type    = "MX"
  ttl     = 3600
  records = ["10 inbound.postmarkapp.com."]
}

resource "powerdns_record" "tx_strategos_gr_spf" {
  zone    = powerdns_zone.strategos_gr.name
  name    = "tx.strategos.gr."
  type    = "TXT"
  ttl     = 3600
  records = ["\"v=spf1 include:spf.mtasv.net ~all\""]
}

resource "powerdns_record" "tx_strategos_gr_dmarc" {
  zone    = powerdns_zone.strategos_gr.name
  name    = "_dmarc.tx.strategos.gr."
  type    = "TXT"
  ttl     = 3600
  records = ["\"v=DMARC1; p=none; rua=mailto:dmarc@rbxsystems.ch; fo=1\""]
}

resource "powerdns_record" "tx_strategos_gr_dkim" {
  count = var.dkim_tx_strategos_gr != "" ? 1 : 0

  zone    = powerdns_zone.strategos_gr.name
  name    = "pm._domainkey.tx.strategos.gr."
  type    = "CNAME"
  ttl     = 3600
  records = [var.dkim_tx_strategos_gr]
}
