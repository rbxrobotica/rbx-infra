# IaC Strategy

**Date:** 2026-04-07
**Status:** Active

## Decision

Two tools. Strictly separated domains. No overlap.

| Tool | Domain | Manages |
|------|--------|---------|
| **Ansible** | Host configuration | OS, services, k3s lifecycle, PowerDNS installation |
| **Terraform** | DNS record state | Zones, A/AAAA/NS/MX/TXT/CNAME records in PowerDNS |

## Rationale

Ansible is already correctly handling machine state. Introducing it as the full IaC
layer for DNS records would require either:
- Ansible tasks calling the PowerDNS API imperatively (no idempotency guarantees),
- or `pdnsutil` commands embedded in shell tasks (drift-prone, no state tracking).

DNS records require declarative state management: what exists in the zone should be
exactly what is declared in code. Terraform's plan/apply cycle and state file provide
this. PowerDNS exposes a REST API that the `pan-net/powerdns` provider consumes.

The decision is pragmatic, not ideological. Ansible is not being replaced. Terraform
is introduced only where its model adds concrete value — state ownership of DNS records.

## Scope boundaries

### Ansible is responsible for

- VPS provisioning preparation (SSH, ufw, fail2ban)
- k3s server and agent installation
- ParadeDB installation on jaguar
- PowerDNS installation and daemon configuration on pantera/eagle
- PowerDNS database creation on jaguar
- DNS-specific firewall hardening (port 53, removal of 80/443)

### Terraform is responsible for

- DNS zone creation (rbxsystems.ch, strategos.gr, future domains)
- All DNS records within those zones:
  - NS, SOA (via zone resource)
  - A, AAAA
  - CNAME (www, DKIM)
  - MX (Postmark inbound)
  - TXT (SPF, DMARC)

### Terraform is NOT responsible for

- VPS creation
- OS-level configuration
- PowerDNS installation or daemon config
- PostgreSQL schema or users
- k3s cluster lifecycle
- Kubernetes resources

### Neither tool manages

- Postmark account or server creation (manual, SaaS)
- Registrar glue records and NS delegation (manual, registrar UI/API)
- Kubernetes Secrets for SMTP credentials (managed via `kubectl` or ExternalSecret)

## File locations

```
rbx-infra/
├── bootstrap/ansible/          # Ansible — host config and service provisioning
└── infra/terraform/dns/        # Terraform — DNS record state
```

## Operational flow

```
1. Ansible provisions pantera/eagle (PowerDNS running, API available)
2. Operator opens SSH tunnel to pantera API:
     ssh -f -N -L 18081:127.0.0.1:8081 root@149.102.139.33
3. Operator runs: terraform init && terraform apply
4. Registrar glue records updated manually (one-time)
5. DKIM values obtained from Postmark, added to terraform.tfvars
6. terraform apply again to create DKIM CNAME records
7. Domain verified in Postmark
```

## Known gaps in current Ansible IaC

### k3s-agent role: token-fact dependency bug

The `k3s-agent` role depends on `hostvars['tiger']['k3s_token']`, which is only set
as a fact during the `k3s-server` play in the same Ansible run. If the server play
is not re-run (e.g., when adding new agents to an existing cluster), the install task
is silently skipped via `when: hostvars['tiger']['k3s_token'] is defined`.

**Workaround used:** Install k3s agents directly with raw SSH commands (idempotent,
since the k3s installer checks the binary via `INSTALL_K3S_EXEC`). Ansible was used
for hardening only.

**Correct fix:** Add a pre-task to the k3s-agent play that reads the token from tiger:
```yaml
- name: Fetch k3s token from server
  slurp:
    src: /var/lib/rancher/k3s/server/node-token
  register: k3s_token_raw
  delegate_to: tiger

- name: Set k3s token fact
  set_fact:
    k3s_token: "{{ k3s_token_raw.content | b64decode | trim }}"
```

This fix should be applied before the next agent join operation.

---

## Adding a new domain in the future

1. Create `infra/terraform/dns/<domain>.tf` following the existing zone file pattern.
2. Add DKIM variables to `variables.tf`.
3. Add DKIM placeholders to `terraform.tfvars.example`.
4. Run `terraform plan` to review, then `terraform apply`.
5. Update registrar delegation.

No Ansible changes required for DNS record additions.

## State file

Terraform state is stored locally (`terraform.tfstate`, gitignored). This is
acceptable for single-operator use. If the infrastructure is ever managed by multiple
people, migrate the backend to S3 or Terraform Cloud before proceeding.
