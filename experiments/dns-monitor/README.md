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
| Current prerequisites | The owner approved project-key creation/custody and reported terminal authentication. Read-only verification found the system monitor installed and timer enabled/active; six service completions matched external success signals. Noninteractive sudo remains unavailable and is not needed for this inspection. No broad API key is deployed in the worker |
| Limits | Owner GPG emergency kit outer-passphrase/offline custody and successors remain incomplete. This new lab signer is disposable and not a production key or founder-independent recovery pass |
| Cost | Existing workstation and Hobbyist allowance; no paid resource selected. Annual ClouDNS assessment follows qualification |

## Implemented control flow and activation boundary

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

## Observed execution

The external missed-heartbeat and recovery emails were delivered, and the check
was restored to 900/900 seconds. A fresh private Git fetch of the encrypted lab
signer restored the same public identity and signed 24 currently valid
authoritative RRsets. Empty recovery keyring, wrong public DS and corrupt
ciphertext were rejected. Six direct local checks and five original-anchor
resolver cases passed after preserving an initial loopback-config failure.

An initial recursive SCP timed out before origin startup; its partial public
cache was removed. A later explicit-member 637,908-byte bundle transferred
successfully. The managed pilot converged to the recovered publication in
14.26 seconds; all 184 exact direct checks and 20 cold validating cases passed.
The temporary origin, control/cache directories and owned firewall rules were
removed, PostgreSQL/k3s stayed active, and the task SSH agent stopped. One-shot
detector delivery changed from failure to healthy and DOWN/UP emails arrived.

The initial native authentication request expired before installation; its
pending receipt remains historical. After the operator reported terminal
authentication and the initial installer's existing-state refusal, read-only
verification found the protected service installed and the enabled timer
**active/waiting**. Six real service completions between 21:05 and 22:00 local
all returned DNS exit 0 and confirmed signals, each matching an external
Healthchecks success within two seconds. The last timer trigger correlated with
the latest successful completion; an off-calendar historical run is not
attributed to the timer. All six installed code/configuration hashes exactly
matched the reviewed files and were root-owned. The external check was up at
900/900 seconds. Gmail contained missed-signal DOWN and activation UP, about
1.75 seconds after the first installed-service completion. The oneshot is
normally inactive between timer runs. No overwrite or additional failure drill
was necessary. Read-only agreement is not a reconstruction of an unavailable
Ansible apply/idempotence transcript.

Evidence and current commitment recommendation are in resilience PR11,
`docs/experiments/2026-11-13-monitor-activation-and-contract-assessment.md` and
`evidence/2026-11-13-monitor-activation-verification/`. Production H-dns remains
unqualified. The worker detects expiry but does not renew signatures; repeatable
renewal/publication, TSIG, independent discovery/vantages and owner-independent
key recovery remain separate work. Current recommendation is to finish the
trial and prefer an initial three-month Premium M term rather than prepay for
the year solely to save USD 4.95. No order was made.

The one-shot stdin credential option is a delivery-drill path, not a persistent
plaintext credential file or substitute for the protected systemd unit.
