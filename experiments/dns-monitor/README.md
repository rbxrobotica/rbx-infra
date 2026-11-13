# Continuous synthetic DNS monitor and owner-only signer recovery

Authorized on 2026-11-13: implement continuous monitoring and test signer
recovery before considering a ClouDNS annual order. No purchase, production
parent NS/DS, production signing key or account factor change is authorized by
this experiment.

## Mandatory state/recovery preflight

| Field | Declared boundary |
| --- | --- |
| State | One named Healthchecks pilot check; a bounded read-only monitor, systemd units and encrypted ping credential; public lab publications; one disposable lab signer in encrypted owner custody |
| Source | This infra feature branch for controller/Ansible/units/signer fixture; resilience PR11 for public references, checker and sanitized observations |
| Remote | Existing private GitHub repositories and encrypted password store; existing Healthchecks Hobbyist project, outside Contabo |
| Backup | Monitor/controller desired config and public inputs versioned remotely; actual lab signer and API/ping credentials only in encrypted `pass` custody with its private Git remote; no plaintext secret file |
| Restore | Existing GPG backup canary passed in a new keyring on November 11; full public signed-zone recovery passed November 13. New signer recovery must use an independently fetched encrypted blob after the seed process exits |
| Protection / retention | Versioned encrypted password store; authenticated transport; public monitor config contains no endpoint token. Deployed encrypted systemd credential is replaceable cache, not the custody source |
| Monitoring | Target one run every 15 minutes, with a 15-minute remote grace period; missing or invalid runs must alarm. Record actual installation and email delivery, not just source configuration |
| RPO / RTO | Public reference is the last reviewed Git publication; detection target at most 30 minutes plus provider delivery. Signer reconstruction target 15 minutes, measured separately from provisioning/independent custody |
| Owner | Leandro; primary executor keeps all secrets, restoration, deployment and failure injection on the governed path |
| Local-only risk | Release/tool caches and encrypted service credential can be reconstructed from Git plus encrypted custody; no canonical private key may exist only in a process or this workstation |
| Current prerequisites | Local user systemd manager runs with linger enabled. Local noninteractive sudo is not available. Existing Healthchecks project has no API key; creating a project management key needs action-time confirmation. No broad API key is deployed in the worker |
| Limits | Owner GPG emergency kit outer-passphrase/offline custody and successors remain incomplete. This new lab signer is disposable and not a production key or founder-independent recovery pass |
| Cost | Existing workstation and Hobbyist allowance; no paid resource selected. Annual ClouDNS assessment follows qualification |

## Planned control flow

`healthchecks-controller.py` reconciles only the exact named check, never deletes
or modifies the sample check, and uses project-specific management access from
encrypted custody only during setup. Its plan is read-only. Runtime gets only
the named check's ping endpoint through an encrypted systemd credential.
The systemd service runs as a restricted DynamicUser, without owner `pass`, GPG
or management API access. The source Ansible playbook declares installation;
review a check/diff before applying. A privileged local authentication step is
needed for system services and credential wrapping. Nothing is installed by
this document alone.

The signer fixture will create a new **lab-only** key, publish its public pin,
encrypt it directly into `pass`, confirm the private remote, end the original
process and restore from a fresh remote ciphertext. Signing renewed data with
the same public key tests technical owner-only recovery. The old November 11
ephemeral signer was destroyed and cannot be recovered. Its failed/expired
observations remain historical evidence.

To test the restored publication on the existing undelegated provider pilot,
reuse the already-reviewed transient origin lifecycle on Jaguar. Only public
signed files and verified official binaries reach that host; the private signer
and owner GPG key remain absent. First recheck port ownership, active PostgreSQL
and k3s-agent, absent prior staging/control state and exact scoped firewall
ownership. Source/public pins must already be versioned remotely. Start only
the recovered phase, verify convergence and cold validation, and execute the
same owned-process/cache/firewall cleanup (20-minute process cap, 30-minute
safety cleanup). No production zone, parent DS/NS or persistent host policy is
changed. This is a lab publication update, not production signing recovery.
