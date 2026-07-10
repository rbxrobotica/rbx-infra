# Zone: rbx.ia.br
# Primary nameserver: pantera (149.102.139.33) — ns1.rbxsystems.ch
# Secondary: eagle (167.86.92.97) — ns2.rbxsystems.ch
# Delegation at registro.br: point NS to ns1.rbxsystems.ch / ns2.rbxsystems.ch

resource "powerdns_zone" "rbx_ia_br" {
  name    = "rbx.ia.br."
  kind    = "Master"
  account = ""

  nameservers = ["ns1.rbxsystems.ch.", "ns2.rbxsystems.ch."]
}

resource "powerdns_record" "rbx_ia_br_ns" {
  zone    = powerdns_zone.rbx_ia_br.name
  name    = "rbx.ia.br."
  type    = "NS"
  ttl     = 86400
  records = ["ns1.rbxsystems.ch.", "ns2.rbxsystems.ch."]
}

# --- Web ---

resource "powerdns_record" "rbx_ia_br_a" {
  zone    = powerdns_zone.rbx_ia_br.name
  name    = "rbx.ia.br."
  type    = "A"
  ttl     = 3600
  records = [var.k3s_ingress_ip]
}

resource "powerdns_record" "www_rbx_ia_br" {
  zone    = powerdns_zone.rbx_ia_br.name
  name    = "www.rbx.ia.br."
  type    = "CNAME"
  ttl     = 3600
  records = ["rbx.ia.br."]
}

# --- Email: root domain (rbx.ia.br) ---

resource "powerdns_record" "rbx_ia_br_mx" {
  zone    = powerdns_zone.rbx_ia_br.name
  name    = "rbx.ia.br."
  type    = "MX"
  ttl     = 3600
  records = ["10 mail.rbxsystems.ch."]
}

resource "powerdns_record" "rbx_ia_br_spf" {
  zone    = powerdns_zone.rbx_ia_br.name
  name    = "rbx.ia.br."
  type    = "TXT"
  ttl     = 3600
  records = ["\"v=spf1 include:spf.mtasv.net ~all\""]
}

resource "powerdns_record" "rbx_ia_br_dmarc" {
  zone    = powerdns_zone.rbx_ia_br.name
  name    = "_dmarc.rbx.ia.br."
  type    = "TXT"
  ttl     = 3600
  records = ["\"v=DMARC1; p=none; rua=mailto:dmarc@rbxsystems.ch; fo=1\""]
}

resource "powerdns_record" "rbx_ia_br_dkim" {
  count = var.dkim_rbx_ia_br != "" ? 1 : 0

  zone    = powerdns_zone.rbx_ia_br.name
  name    = "pm._domainkey.rbx.ia.br."
  type    = "CNAME"
  ttl     = 3600
  records = [var.dkim_rbx_ia_br]
}

# --- Internal tooling ---

resource "powerdns_record" "api_comms_rbx_ia_br_a" {
  zone    = powerdns_zone.rbx_ia_br.name
  name    = "api.comms.rbx.ia.br."
  type    = "A"
  ttl     = 3600
  records = [var.k3s_ingress_ip]
}

resource "powerdns_record" "cms_rbx_ia_br_a" {
  zone    = powerdns_zone.rbx_ia_br.name
  name    = "cms.rbx.ia.br."
  type    = "A"
  ttl     = 3600
  records = [var.k3s_ingress_ip]
}

resource "powerdns_record" "grafana_rbx_ia_br_a" {
  zone    = powerdns_zone.rbx_ia_br.name
  name    = "grafana.rbx.ia.br."
  type    = "A"
  ttl     = 3600
  records = [var.k3s_ingress_ip]
}

resource "powerdns_record" "robson_rbx_ia_br_a" {
  zone    = powerdns_zone.rbx_ia_br.name
  name    = "robson.rbx.ia.br."
  type    = "A"
  ttl     = 3600
  records = [var.k3s_ingress_ip]
}

resource "powerdns_record" "strategos_rbx_ia_br_a" {
  zone    = powerdns_zone.rbx_ia_br.name
  name    = "strategos.rbx.ia.br."
  type    = "A"
  ttl     = 3600
  records = [var.k3s_ingress_ip]
}

resource "powerdns_record" "eden_rbx_ia_br_a" {
  zone    = powerdns_zone.rbx_ia_br.name
  name    = "eden.rbx.ia.br."
  type    = "A"
  ttl     = 3600
  records = [var.k3s_ingress_ip]
}

resource "powerdns_record" "api_eden_rbx_ia_br_a" {
  zone    = powerdns_zone.rbx_ia_br.name
  name    = "api.eden.rbx.ia.br."
  type    = "A"
  ttl     = 3600
  records = [var.k3s_ingress_ip]
}

resource "powerdns_record" "ledger_rbx_ia_br_a" {
  zone    = powerdns_zone.rbx_ia_br.name
  name    = "ledger.rbx.ia.br."
  type    = "A"
  ttl     = 3600
  records = [var.k3s_ingress_ip]
}

resource "powerdns_record" "maestro_rbx_ia_br_a" {
  zone    = powerdns_zone.rbx_ia_br.name
  name    = "maestro.rbx.ia.br."
  type    = "A"
  ttl     = 3600
  records = [var.k3s_ingress_ip]
}

resource "powerdns_record" "agents_rbx_ia_br_a" {
  zone    = powerdns_zone.rbx_ia_br.name
  name    = "agents.rbx.ia.br."
  type    = "A"
  ttl     = 3600
  records = [var.k3s_ingress_ip]
}

resource "powerdns_record" "commerce_rbx_ia_br_a" {
  zone    = powerdns_zone.rbx_ia_br.name
  name    = "commerce.rbx.ia.br."
  type    = "A"
  ttl     = 3600
  records = [var.k3s_ingress_ip]
}

resource "powerdns_record" "creator_rbx_ia_br_a" {
  zone    = powerdns_zone.rbx_ia_br.name
  name    = "creator.rbx.ia.br."
  type    = "A"
  ttl     = 3600
  records = [var.k3s_ingress_ip]
}
