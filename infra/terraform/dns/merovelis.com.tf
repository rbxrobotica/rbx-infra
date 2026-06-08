# Zone: merovelis.com
# Primary web/auth surface for Merovelis and canonical Strategos app host.

resource "powerdns_zone" "merovelis_com" {
  name    = "merovelis.com."
  kind    = "Master"
  account = ""

  nameservers = ["ns1.rbxsystems.ch.", "ns2.rbxsystems.ch."]
}

# --- NS ---

resource "powerdns_record" "merovelis_com_ns" {
  zone    = powerdns_zone.merovelis_com.name
  name    = "merovelis.com."
  type    = "NS"
  ttl     = 86400
  records = ["ns1.rbxsystems.ch.", "ns2.rbxsystems.ch."]
}

# --- Web ---

resource "powerdns_record" "merovelis_com_a" {
  zone    = powerdns_zone.merovelis_com.name
  name    = "merovelis.com."
  type    = "A"
  ttl     = 3600
  records = [var.k3s_ingress_ip]
}

resource "powerdns_record" "www_merovelis_com" {
  zone    = powerdns_zone.merovelis_com.name
  name    = "www.merovelis.com."
  type    = "CNAME"
  ttl     = 3600
  records = ["merovelis.com."]
}

resource "powerdns_record" "app_merovelis_com" {
  zone    = powerdns_zone.merovelis_com.name
  name    = "app.merovelis.com."
  type    = "A"
  ttl     = 3600
  records = [var.k3s_ingress_ip]
}

resource "powerdns_record" "auth_merovelis_com" {
  zone    = powerdns_zone.merovelis_com.name
  name    = "auth.merovelis.com."
  type    = "A"
  ttl     = 3600
  records = [var.k3s_ingress_ip]
}
