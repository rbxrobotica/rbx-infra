# Spaceship DNS migration, inventoried 2026-11-04.
# Keep parking records until each site has its own cutover.
# Delegate only after both RBX nameservers serve signed zones and the matching
# DS records have been prepared at Spaceship.
# DNSSEC keys/metadata are outside the PowerDNS provider 1.5 zone schema.

locals {
  spaceship_domains = toset([
    "initelo.com",
    "iteruno.com",
    "satwake.com",
    "verentir.com",
    "zugbrief.com",
  ])
}

resource "powerdns_zone" "spaceship" {
  for_each = local.spaceship_domains

  name         = "${each.key}."
  kind         = "Master"
  account      = ""
  soa_edit_api = "DEFAULT"
  nameservers  = ["ns1.rbxsystems.ch.", "ns2.rbxsystems.ch."]
}

resource "powerdns_record" "spaceship_ns" {
  for_each = local.spaceship_domains

  zone    = powerdns_zone.spaceship[each.key].name
  name    = powerdns_zone.spaceship[each.key].name
  type    = "NS"
  ttl     = 86400
  records = ["ns1.rbxsystems.ch.", "ns2.rbxsystems.ch."]
}

resource "powerdns_record" "spaceship_parking_a" {
  for_each = local.spaceship_domains

  # Both RRsets update the same SOA. Serialize these groups to avoid concurrent
  # PowerDNS/PostgreSQL serial updates while creating a zone.
  depends_on = [powerdns_record.spaceship_ns]

  zone    = powerdns_zone.spaceship[each.key].name
  name    = powerdns_zone.spaceship[each.key].name
  type    = "A"
  ttl     = 300
  records = each.key == "initelo.com" ? ["158.220.116.31"] : ["34.216.117.25", "54.149.79.189"]
}

resource "powerdns_record" "initelo_www" {
  depends_on = [powerdns_record.spaceship_parking_a]

  zone    = powerdns_zone.spaceship["initelo.com"].name
  name    = "www.initelo.com."
  type    = "CNAME"
  ttl     = 300
  records = ["initelo.com."]
}
