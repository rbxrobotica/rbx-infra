# DNS Sovereign Architecture

**Audience:** engineers and SREs who need to understand or
troubleshoot the RBX DNS infrastructure.

For operational procedures (adding records, running tofu), see
`docs/infra/DNS.md`. For troubleshooting, see
`docs/runbooks/DNS-TROUBLESHOOTING.md`.

---

## Topology

```
                    ┌──────────────────────────────┐
                    │        .ch TLD registry       │
                    │  NS: ns1.rbxsystems.ch        │
                    │      ns2.rbxsystems.ch        │
                    └──────────┬───────────────────┘
                               │ delegation
              ┌────────────────┼────────────────┐
              │                │                │
    ┌─────────▼─────────┐     │     ┌──────────▼────────┐
    │     pantera        │     │     │      eagle        │
    │   ns1.rbxsystems.ch│     │     │  ns2.rbxsystems.ch│
    │   149.102.139.33   │     │     │   167.86.92.97    │
    │                    │     │     │                    │
    │  PDNS Master       │     │     │  PDNS Slave       │
    │  Backend: gpgsql   │     │     │  Backend: bind    │
    │         │          │     │     │                    │
    └─────────┼──────────┘     │     └────────────────────┘
              │                │              ▲
              │                │              │ AXFR/IXFR
              │                │              │ (zone transfer)
              │                └──────────────┘
              │                NOTIFY on zone change
              ▼
    ┌──────────────────────┐
    │   jaguar (161.97.    │
    │   147.76)            │
    │                      │
    │   PostgreSQL         │
    │   DB: pdns           │
    │   User: pdns         │
    │   Port: 5432         │
    │                      │
    │   Also: k3s agent,   │
    │   ParadeDB host      │
    └──────────────────────┘

    Operator workstation
    ┌──────────────────────┐
    │ tofu → SSH tunnel    │
    │ 127.0.0.1:18081 ──►  │──► pantera:8081 (PDNS API)
    │ pass → GPG secrets   │
    └──────────────────────┘
```

---

## Design rationale

### Why two DNS servers?

DNS requires at least two authoritative nameservers for reliability
and compliance with registrar requirements. A single server is a
single point of failure — if it goes down, all zones under it become
unresolvable.

### Why Postgres backend on pantera?

PowerDNS supports multiple backends (bind files, database, etc.). The
gpgsql backend was chosen because:

- **Transactional consistency:** database writes are atomic, no
  corrupted zone files.
- **API-driven:** the PowerDNS HTTP API reads from the database,
  enabling Terraform/OpenTofu to manage records programmatically.
- **Audit trail:** every record change goes through tofu state,
  which is version-controlled.

### Why bind backend on eagle?

The secondary uses bind (zone files) because:

- **No database dependency:** eagle doesn't need a Postgres
  connection, reducing failure modes.
- **AXFR simplicity:** pantera sends NOTIFY, eagle pulls the full
  zone via AXFR and writes it to a local file.
- **Isolation:** if jaguar's Postgres goes down, eagle still serves
  the last-known-good zone data from its local file.

### Why Postgres external to k3s?

The PDNS database must survive k3s cluster restarts, upgrades, and
misconfigurations. Running it inside k3s would create a circular
dependency: the cluster needs DNS to resolve its own services, but
DNS needs the cluster to be running to reach its database.

This follows the RBX infrastructure rule: PostgreSQL never runs inside
the production k3s cluster.

---

## Replication flow

```
1. Operator runs tofu apply
       │
       ▼
2. tofu → PDNS API (pantera:8081)
       │  Creates/updates records in PostgreSQL
       ▼
3. pantera detects zone change
       │
       ▼
4. pantera sends DNS NOTIFY to eagle (also-notify=167.86.92.97)
       │
       ▼
5. eagle receives NOTIFY, initiates AXFR from pantera
       │
       ▼
6. eagle writes zone data to /var/lib/powerdns/zones/rbxsystems.ch.zone
       │
       ▼
7. Both nameservers serve the updated zone
```

If NOTIFY fails (e.g., pantera was restarting), force manually:

```bash
# On pantera:
pdns_control notify rbxsystems.ch

# On eagle:
pdns_control retrieve rbxsystems.ch
```

---

## Zones served

| Zone | NS1 | NS2 | Records |
|------|-----|-----|---------|
| `rbxsystems.ch` | pantera | eagle | Web, email, grafana, robson |
| `strategos.gr` | pantera | eagle | Strategos product |

Zone records are defined in `infra/terraform/dns/rbxsystems_ch.tf`
and `infra/terraform/dns/strategos_gr.tf`.

---

## Recovery scenarios

| Scenario | Impact | Recovery |
|----------|--------|----------|
| pantera down | ns1 unreachable; ns2 continues serving cached zone | Fix pantera, `systemctl restart pdns` |
| jaguar Postgres down | pantera cannot load zones; eagle still serves | Fix Postgres; see `DNS-TROUBLESHOOTING.md` |
| gpgsql password stale | pantera crash loop | Update `/etc/powerdns/pdns.conf`, restart pdns |
| eagle out of sync | stale records on ns2 | `pdns_control retrieve <zone>` on eagle |
| tofu state drift | records differ from git | `tofu plan` to inspect, `tofu apply` to reconcile |

For detailed troubleshooting steps, see
`docs/runbooks/DNS-TROUBLESHOOTING.md`.
