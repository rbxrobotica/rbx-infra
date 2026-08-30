# Runbook: Headscale mesh (ADR-0011)

Sovereign WireGuard mesh: headscale 0.29.3 on eagle, embedded DERP,
pinned tailscale 1.102.3 clients. **Everything in phases 1 to 3 is
additive**; nothing existing is closed or changed. Phase 4 (closing
ports) is out of scope here and happens in separate PRs, one service
and one host at a time.

## Invariants (never break these)

- `--accept-dns=false` on every node. The mesh never touches
  resolv.conf. pantera and eagle ARE the DNS servers.
- Auth by pre-auth keys generated on eagle, TTL 15 minutes, never
  stored in any repo, inventory, or pass entry.
- No OIDC against ZITADEL (circular dependency: identity would depend
  on the network that protects identity).
- Public SSH stays open everywhere until phase 4, which is not this
  runbook. The Contabo VNC console is the break-glass of last resort.
- Versions are pinned and apt-held. Upgrades are PRs.

## Phase 1: control plane on eagle

Prerequisite: the DNS records exist (tofu apply in
`infra/terraform/dns/`, records `headscale.rbxsystems.ch` A + AAAA).

```
cd bootstrap/ansible
ansible-playbook headscale.yml -i inventory/hosts.yml --limit eagle
```

The role validates the config (`headscale configtest`) before starting
anything and ends by polling `https://headscale.rbxsystems.ch/health`
until it answers 200. Then create the single user (namespace) for the
fleet:

```
ssh eagle 'headscale users create rbx'
```

Note the user's numeric id (the first created user is 1; confirm with
`headscale users list --output json`). The 0.29 CLI takes ids, not
names, in `--user` flags.

Verification: `ssh eagle 'headscale nodes list'` (empty list, no
error); `curl -sS https://headscale.rbxsystems.ch/health`.

Rollback: `ssh eagle 'systemctl stop headscale'`. Nothing else depends
on it yet.

## Phase 2: enroll nodes, one at a time

Order (lowest risk first): bengal, corbetti, pantera, eagle itself,
altaica, sumatrae, tiger, jaguar, lince, then the workstation.

For each host, one PR adds it to the `tailscale_nodes` group in
`inventory/hosts.yml`. After merge:

```
cd bootstrap/ansible
ansible-playbook headscale.yml -i inventory/hosts.yml \
  --limit <host> \
  --extra-vars "tailscale_authkey=$(ssh eagle 'headscale preauthkeys create --user 1 --expiration 15m --output json' | jq -r .key)"
```

`--user 1` is the numeric id of the `rbx` user created in phase 1.
Without `tailscale_authkey` the play only installs and holds the
client. The key expires in 15 minutes and is single-use by default.

Verification per host, before moving to the next:

```
ssh <host> 'tailscale status'          # BackendState Running, peers visible
ssh <host> 'tailscale ip -4'           # 100.64.x.x assigned
ping -c3 <mesh-ip-of-host>             # from an already-enrolled node
ssh <host-public-ip> true              # public path STILL works
```

First node exception: with one node enrolled there are no peers and no
other node to ping from. For node one, "verified" means BackendState
Running, a 100.64.x.x address assigned, and the node visible in
`ssh eagle 'headscale nodes list'`. The peer and ping checks apply from
the second node onward (and retroactively confirm node one).

Rollback per host: `ssh <host> 'tailscale down'` (leaves the mesh,
public access untouched). Full removal: `tailscale logout`, then
`apt remove tailscale` and
`ssh eagle 'headscale nodes delete --identifier <id>'` (ids from
`headscale nodes list`).

The workstation is not in the Ansible inventory; enroll it manually
with the same invariants, after every server is stable:

```
curl -fsSL https://tailscale.com/install.sh | sh   # or distro package, then pin/hold
sudo tailscale up \
  --login-server=https://headscale.rbxsystems.ch \
  --accept-dns=false --accept-routes=false \
  --authkey=$(ssh eagle 'headscale preauthkeys create --user 1 --expiration 15m --output json' | jq -r .key)
```

## Phase 3: dual path

Add mesh IPs as SSH alternates in `~/.ssh/config` (new Host aliases
like `tiger-mesh`, do not touch the existing entries). Operate normally
for at least a week. The mesh must prove itself while changing nothing.

What headscale downtime does in this phase: existing WireGuard tunnels
keep working (peers cache each other); new joins, key renewals and
relay via DERP stop until it returns. That is the accepted blast
radius of hosting it on one VPS.

## Phase 4 (NOT here): closing ports

Each of these is its own future PR with its own window and rollback,
only after phase 3 has held:

1. jaguar 5432: pg_hba + UFW from k3s node public IPs to mesh IPs, and
   the ZITADEL Helm values switch the DB host to jaguar's mesh IP (pod
   restart, minutes of login downtime, reversible).
2. SSH per host, one at a time, keeping public SSH on eagle (or the
   provider console) as the anchored escape.

Never mesh-gate: 53/udp+tcp on pantera and eagle, mail ports on lince,
80/443 on the k3s ingress. Those are public by design.

## Audit linkage

When phase 2 completes, rbx-resilience gains the `headscale` component
record (v0.3): class deterministic-artifact, mesh-loss drill, plain
WireGuard as `fallback_policy`, and eagle's double duty noted in
failure domains.
