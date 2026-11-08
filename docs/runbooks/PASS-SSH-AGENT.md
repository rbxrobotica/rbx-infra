# On-demand identity activation from pass

`scripts/network/pass_ssh_agent.py` reuses one public identity and the local
agent already declared by an existing SSH alias. It never starts an agent or
connects to a server. Source preparation and mocked tests do not install or
activate this helper; an operator installs a user-local entry point separately.

```sh
python3 scripts/network/pass_ssh_agent.py status --host <existing-alias>
python3 scripts/network/pass_ssh_agent.py load --host <existing-alias> --pass-entry <registered-pass-entry> --ttl 900
```

The alias must resolve through `ssh -G` to exactly one existing `IdentityFile`
ending in `.pub`, an explicit absolute `IdentityAgent`, strict
host checking (`yes` or OpenSSH's `true`), `ForwardAgent no`, and
`IdentitiesOnly yes`. The identity path must be absolute or start with `~/`,
which expands using the caller's passwd home directory, independently of `HOME`.
Other users' tildes and `$`/`%` expansion tokens are rejected.
The public file must belong to the caller and must not
be writable by group or others. The existing agent socket must belong to the
caller, reside beneath the caller's private `XDG_RUNTIME_DIR` (or the normal
Linux per-user runtime directory), and have private directory/socket modes.
Symlinks and unsupported paths or file types fail closed.

`status` reads the configured public key and compares its SHA256 fingerprint
with that agent's identity list. It never reads pass. `load` first performs
the same check and returns unchanged if the expected identity is present.
Otherwise it reads only the supplied pass entry into bounded process memory.
GPG may require the operator to unlock its existing key. Exactly one standard
OpenSSH private-key block, at most 32 KiB, is accepted. The derived public
algorithm and base64 key must exactly match the configured public file before
the private bytes can reach `ssh-add` over stdin.

Newly loaded keys receive the requested lifetime: 900 seconds by default,
with an allowed range of 60 to 3600 seconds. An already loaded identity is a
no-op: its existing lifetime is neither renewed nor verified. Activation is
on demand; there is no background service or automatic indefinite cache.
Pass remains the source of truth. Process memory and the existing agent are
temporary copies, and this helper does not back up or alter credential custody.

Output is JSON containing a fixed `code` and four booleans:
`configuration_valid`, `agent_available`, `expected_key_loaded`, and `changed`.
`ready` and `loaded` exit successfully; `key_missing` and failures exit 1.
No public fingerprints, local paths, key contents, pass references, or raw
command errors are printed. Commands use fixed argument arrays, bounded output
and timeouts, and the exact validated agent socket. No other identities are
removed. SSH configuration, host trust, server permissions, and
`authorized_keys` remain outside this helper's actions.

Run the synthetic test suite without invoking real SSH, pass, or ssh-agent:

```sh
python3 -m unittest discover -s scripts/tests -p test_pass_ssh_agent.py
```

## Verified operator binding, 2026-11-08

The operator workspace has a user-local `rbx-eagle-access` entry point bound to
the existing Eagle alias and existing encrypted credential. Its source copy
matches commit `11a35d4`, SHA-256
`ad86db9aeb17ee7a1fb86e03644a453f24160e6d7445b30d47bd9281f1c00b00`.
The installation is a reproducible source copy and local binding; pass remains
the credential source. Private paths and credential references are not published.

```sh
rbx-eagle-access status
rbx-eagle-access load
ssh eagle
```

The initial cache was empty at verification. The installed `load` then reported a
changed, matching identity; `status` reported ready, and a separate strict,
non-forwarding SSH probe returned the Headscale service active. The installed
binding uses a 900-second lifetime. GPG unlock and an existing running agent
are prerequisites; there is no automatic permanent cache or new server grant.
Three existing SSH configuration files were inspected without finding a
`Match exec` directive. As `ssh -G` evaluates configuration, keep that source
trusted and review changes before credential activation.

These observations qualify this local authentication workflow only. Independent
GPG recovery custody and replication of the exact encrypted credential were
not established by this login test. They remain recovery preflight items.
