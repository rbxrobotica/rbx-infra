# ADR-0010: bengal as the declared Ubuntu 26.04 pilot node

## Status

**Accepted conditionally** — 2026-08-06

Conditional because the governance rule below leans on a daily control that
does not exist yet: it lives in an open, and currently blocked, pull request
(rbx-security#3), it is not on `main`, and its timer is not installed anywhere.
This ADR becomes **Accepted** when that control is merged, installed and
observed running. Until then the rule is an intent, not an enforced control.

## Context

bengal (164.68.96.68) joined the cluster as a k3s agent on 2026-03-27, was
flagged compromised on 2026-03-29, and was removed from the cluster on
2026-04-07. It was never reinstalled. It stayed powered on and reachable,
absent from the Ansible inventory and therefore receiving no hardening, until
2026-08-06.

A forensic pass on 2026-08-06 found the machine genuinely idle (no service
beyond the stock Ubuntu set, empty `/home` and `/opt`, no cron, no timer, no
container, only port 22 listening) and found no residual artifact. It also
found no successful SSH login from any address other than the operator's across
the entire incident window, despite more than 100k password attempts. The
nature of the original compromise is not documented anywhere, and the
containerd storage that would have held the evidence went away with the k3s
uninstall on 2026-04-08.

Absence of artifact is not proof of cleanliness. The OS was reinstalled from
scratch rather than cleaned, because reinstalling costs minutes on a machine
that holds nothing and removes the doubt instead of managing it.

The reinstall came back as **Ubuntu 26.04 LTS**. The other seven fleet hosts
run **Ubuntu 24.04.4 LTS**.

### What was verified before accepting the divergence

- `sshd` socket activation is already in use on 24.04 (tiger has `ssh.socket`
  active), so it is not a new behaviour introduced by 26.04, and
  `systemctl reload ssh` responds correctly on bengal.
- The packages the `hardening` role installs (`ufw`, `fail2ban`,
  `unattended-upgrades`) are present in 26.04.
- `bootstrap/ansible/site.yml` carries no OS version assert, so nothing in the
  automation blocks the newer release.

## Decision

bengal rejoins as a **k3s agent on Ubuntu 26.04**, and that divergence is
recorded as a **deliberate pilot** for a future fleet-wide migration, not as an
accident of which image was selected in the provider panel.

Two alternatives were considered and rejected:

| Option | Why not |
|--------|---------|
| Reinstall with 24.04 to match the fleet | Safest, and it was the standing recommendation, but it spends the opportunity: the fleet has to move to 26.04 eventually, and a disposable node carrying no workload is the cheapest place to learn what breaks. |
| Proceed on 26.04 without recording anything | This is how bengal got lost the first time. An undocumented divergence is indistinguishable from a mistake six months later. |

### Why agent and not a fourth control plane

etcd fault tolerance follows quorum, which is the majority of members. Three
members give a quorum of two and tolerate one loss. Four members raise the
quorum to three and still tolerate **one** loss, while adding write latency
because every commit must reach three nodes instead of two. Tolerating two
losses needs five members.

The real constraint is memory: the three control planes carry 76 pods across
24GB combined, with sumatrae between 86% and 88%, while jaguar holds 24GB and
runs two pods because it is reserved by
`robson.io/dedicated=analytics:NoSchedule`.

### Why the pilot carries a taint

The first version of this ADR had bengal join untainted, to take pressure off
the control planes immediately. That contradicts the premise that makes the
pilot cheap: a node whose rollback is "reinstall it" cannot be holding whatever
the scheduler happened to place there, and `local-path` is the cluster's default
storage class, so an arbitrary PVC would bind data to this node.

bengal therefore joins with `rbx.io/os-pilot=ubuntu-2604:NoSchedule`. Workloads
opt in with a matching toleration, starting with stateless canaries. The
relief the control planes need comes as tolerations are added deliberately, not
as a side effect of the node existing. The taint comes off when the pilot is
accepted.

## Consequences

### Accepted costs

- Role and playbook changes must be validated against two Ubuntu releases until
  the fleet converges.
- bengal's kernel and package cadence differ from the rest of the fleet, so
  `unattended-upgrades` will not move in lockstep with the other nodes.
- Any incident on bengal carries one extra variable that an incident on a
  24.04 node does not.

### Pilot evaluation criteria

The pilot is judged on these, reviewed at 30 and 90 days:

1. The `hardening` role applies with no 26.04-specific change.
2. The node stays `Ready` and runs scheduled workloads without container
   runtime, AppArmor or cgroup differences surfacing.
3. `unattended-upgrades` does not force reboots at a cadence the other nodes
   avoid.
4. The daily fleet check (rbx-security `scripts/fleet-healthcheck.sh`) reports
   no finding specific to this host.

If all four hold at 90 days, the fleet migration to 26.04 becomes a planned
project with bengal as the reference. If any fails, bengal is reinstalled on
24.04 and this ADR is superseded.

### Governance

The four-month gap during which a host flagged as compromised stayed reachable
and unmanaged is the failure this ADR also addresses. The rule it establishes:
**a machine that exists is either in the Ansible inventory or is powered off.**

That rule is currently **unenforced**. The intended control is the daily fleet
check in rbx-security, which reports any reachable host absent from the
inventory, but it is still an open pull request and its timer is not installed.
Even as designed it compares only the Ansible inventory, the SSH config and the
cluster nodes, so a machine missing from all three stays invisible; closing that
needs an authoritative asset source, which means the provider API. Until a
control is merged, installed and observed, the rule holds only as long as
somebody remembers it.

## References

- Reinstall and rejoin procedure: rbx-security
  `docs/runbooks/host-reinstall-rejoin.md`
- Daily detection: rbx-security `docs/runbooks/fleet-daily-healthcheck.md`
- Original decommission record: `docs/PLAN-dns-email-architecture.md`,
  `docs/PENDING-2026-03-29.md`
