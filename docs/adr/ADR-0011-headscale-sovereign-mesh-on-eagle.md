# ADR-0011: sovereign WireGuard mesh via Headscale on eagle

## Status

**Accepted** · 2026-08-30

## Context

The resilience audit (rbx-resilience) surfaced two related facts:

1. Fleet administration is SSH direct on public IPs, and jaguar's
   Postgres (ZITADEL, PowerDNS backends) listens on a public IP with
   UFW/pg_hba as the only barrier.
2. The audit scaffold assumed a `headscale` dependency that does not
   exist: no host runs it. Verified on 2026-08-30 on 8 of the 9 fleet
   hosts (tiger, jaguar, altaica, sumatrae, corbetti, bengal, pantera,
   eagle); lince was not in that check loop and gets verified at its
   own enrollment, and no repo contains any headscale configuration.

A mesh VPN closes administrative and database ports from the public
internet. The sovereignty requirement rules out Tailscale SaaS: a
vendor-hosted control plane is a revalidation-class dependency (see
rbx-resilience ADR 0004 for the vocabulary), and RBX's rule is that
nothing required to rebuild RBX may depend exclusively on a third
party's running service.

Headscale is the self-hosted, BSD-licensed control server for the
Tailscale protocol. What remains third-party after self-hosting:

- The `tailscaled` client, developed by Tailscale Inc. (open source on
  Linux). Headscale reimplements their control protocol and follows it.
- The public DERP relay map, used by default for NAT traversal.

Both are closed off below. The data plane is WireGuard; the exit
strategy (plain WireGuard with static peers) exists regardless of
either project's future.

## Decision

Deploy Headscale **0.29.3** (deb pinned by sha256) on **eagle**
(167.86.92.97), the PowerDNS secondary, as a systemd service with
SQLite state in `/var/lib/headscale/`.

Sovereignty conditions, all mandatory:

1. **Embedded DERP only.** `derp.server.enabled: true`, `derp.urls: []`.
   No traffic ever transits Tailscale Inc. infrastructure. STUN on
   3478/udp, relay over the control listener on 443.
2. **Control URL is a DNS name**, `headscale.rbxsystems.ch`, added to
   the zone code. Moving the control plane later (v0.3 provider
   diversity: a non-Contabo micro-VPS) is a DNS change plus one SQLite
   file copy, transparent to enrolled nodes.
3. **Pre-auth keys, not OIDC.** Wiring Headscale auth to ZITADEL would
   make identity depend on the network that protects identity: a
   circular dependency the audit model forbids. Keys are generated
   ad hoc on eagle and never stored in any repository.
4. **Pinned versions.** headscale 0.29.3 (sha256 in the role defaults),
   tailscale client 1.102.3 with the apt package held. Upgrades are
   deliberate PRs, never unattended.
5. **`--accept-dns=false` on every node.** MagicDNS stays off; the mesh
   never touches resolv.conf. Non-negotiable on pantera and eagle,
   which are the authoritative DNS servers themselves.

Placement rationale: eagle is the lightest-duty host (PDNS secondary),
has 3.9 GB RAM with ~600 MB used, free 443/3478, public IPv4+IPv6, and
is not a k3s node, so the trust layer does not live inside the thing it
protects. corbetti was rejected: it is the agent workbench with scoped
write kubeconfigs (ADR-0009), and autonomous agents and mesh control
must not share a host. Known accepted risk: eagle is Contabo like the
rest of the fleet, so the mesh shares the provider failure domain until
the v0.3 off-provider move; acceptable because the mesh is additive and
existing WireGuard tunnels survive control-plane downtime.

Rollout is **additive only**, one host per run (`serial: 1`, explicit
`--limit`): install client, join mesh, verify dual path (mesh SSH in
parallel with public SSH). **This ADR and its playbook close nothing.**
Firewall tightening (jaguar 5432 to mesh IPs, SSH per host) is a later
phase, one service and one host at a time, each its own PR, after days
of proven dual-path operation. Public-by-design ports (53 on
pantera/eagle, mail on lince, 80/443 ingress) are never mesh-gated.

## Alternatives considered

- **Tailscale SaaS**: best UX, rejected: vendor control plane holds the
  network's identity and ACLs; revalidation-class, not restorable.
- **Netbird**: client and server from one open project (no protocol
  chasing); younger, smaller ecosystem. Revisit at the v0.3 review if
  headscale's upstream-following becomes a real cost.
- **Nebula**: fully self-contained, but no NAT-traversal-free UX for
  workstations and a separate CA workflow to operate.
- **Plain WireGuard**: maximal sovereignty, all-manual key and peer
  management for 9+ nodes; retained as the documented fallback, not the
  operating mode.

## Consequences

- eagle gains two duties (PDNS secondary, mesh control). Failure of
  eagle degrades new mesh joins and relay, not existing tunnels, and
  not DNS (pantera is primary).
- The fleet gains a second path to every host before anything closes.
- rbx-resilience gains a real `headscale` component in v0.3 (class
  deterministic-artifact, mesh-loss drill, plain-WireGuard fallback
  policy); until then the audit keeps saying the truth: it records the
  ZITADEL dependency on headscale as removed, because it did not exist.
- Runbook: `docs/runbooks/HEADSCALE-MESH.md`.
