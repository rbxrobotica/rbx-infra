# Headscale adoption candidate and isolated validation

Status: preparation only, October 12, 2026. This increment prepares an unchanged
configuration and policy for review. It cannot deploy, restart, enroll a device,
change a grant or remove the [legacy rollout quarantine](HEADSCALE-RECONCILIATION.md).

## Source and observed baseline

The recovered ThinkCentre source at `4f84a1a0d2f5526e3d1c825baaa75f38cf030ef0`
contains the template introduced by `effc982d94138d35ef12907f2d0a31b19cc90cb2`,
blob `69a8e23973a414f6bce28316664893ad4c03a0a9`. Rendering that historical
template with the observed profile reproduces Eagle's configuration SHA-256
exactly. The recovered policy also reproduces its observed hash. These inputs
were reconstructed from Git; no production keys or database were copied.

| File | Observed and reconstructed SHA-256 |
| --- | --- |
| Configuration | `d138ed7be87af3d20440e63d042fe3d7f988e3850e646530af632cc35c2126ff` |
| Policy | `19920f2d76129cce5401a12ceb25ff8516ff42970cc581a725564b259675dda1` |

The new template retains the profile and corrects comments only. It does not
import the historical ADR number or assert that server DNS settings prove
client behavior. The bundle keeps the original configuration bytes as well as
the policy bytes; different comments in the rendered candidate do not alter
the bundle. A future semantic change needs a separate reviewed implementation.

At 23:32 BRT on October 12, the active process had no `HEADSCALE_*` environment
variables or explicit `--config` argument. This supports the observed default
lookup profile. It does not prove which policy bytes are currently in memory,
eliminate all runtime dependencies or replace a fresh pre-promotion inspection.
See the [sanitized evidence](../network/local/evidence/headscale-candidate-2026-10-12.json).

## Local preparation contract

The playbook `bootstrap/ansible/headscale-candidate.yml` runs on `localhost`,
with a local connection, no privilege escalation and no server inventory.
It renders only into new private staging. The Python builder requires:

- Operator-attested consistent private copies or stopped sources, with known
  configuration and policy hashes supplied independently of the candidate.
- An explicit absolute logical policy path matching both YAML inputs. The
  copied policy can reside elsewhere; the logical path is never opened.
- Complete, type-sensitive equality of the parsed current and candidate YAML.
  A DNS, port, path or unknown-field change is rejected, as is `false` to `0`.
- A finite nonempty policy passing the bounded file preflight. Unsupported
  HuJSON, missing/empty rules, broad wildcard rules and malformed input fail.
- A new output directory beneath an existing user-owned `0700` parent.
  Existing output, symlinks and overlapping inputs/output fail closed.

Source fingerprints and hashes are checked repeatedly. This detects observed
changes; it is not an atomic snapshot of a running service. The receipt states
this limit. Generated files are `0600`; the new bundle directory is `0700`.
On failure the builder removes only the files it created, never an existing or
replacement file. A failed cleanup is reported and requires local inspection.

Prepare a private variables file using these fields, replacing placeholders
with independently verified values. Do not commit the inputs or variables file:

```yaml
headscale_candidate_current_config: /private/snapshot/config.yaml
headscale_candidate_policy: /private/snapshot/policy.hujson
headscale_candidate_expected_config_sha256: "<verified-config-sha256>"
headscale_candidate_expected_policy_sha256: "<verified-policy-sha256>"
headscale_candidate_output_dir: /private/candidates/new-adoption-bundle
```

From the repository root, with Ansible, Python 3.10+ and PyYAML available:

```sh
ansible-playbook -i localhost, bootstrap/ansible/headscale-candidate.yml \
  --extra-vars @/private/candidate-inputs.yml
```

This generator uses normal mode; check mode is rejected. A successful receipt
has `status: prepared`, `adoption_only: true`, `policy_preserved: true`,
`native_validation: false` and `live_changes: false`. The output consists of
`config.yaml`, `policy.hujson` and a sanitized `receipt.json`, written last.
Check the command result as well as the receipt. Do not copy the bundle into
`/etc/headscale`; promotion and rollback are not implemented here.

## Reproducible native tests

```sh
python3 -B -m unittest discover -s scripts/tests -p 'test_headscale_*.py' -v
scripts/network/test-headscale-candidate-native.sh
```

The [fixture guide](../../scripts/tests/fixtures/headscale/README.md) lists
prerequisites and the verified-release cache option. The native harness accepts
only synthetic repository inputs, not an operational bundle. It verifies the
official Headscale 0.29.3 archive by SHA-256 and runs it inside rootless Podman
with a digest-pinned Ubuntu image. The container has no network, no published
ports, no capabilities, a read-only root filesystem and three read-only bind
mounts. Database, generated keys and sockets exist solely in disposable tmpfs.
The environment is cleared; host home, sockets and production state are absent.

Two users and three nodes are seeded through a temporary synthetic daemon with
a separate deny-all bootstrap policy. That daemon is stopped before exclusive
direct database access. Native `configtest` and `policy check` then consume the
complete prepared candidate without rewriting any runtime field or path.
The test matrix asserts two accepted and six denied TCP flows. Invalid schema,
missing identity in an expected-allow test, and swapped grants must fail.
Missing/empty policy and absent/empty rule containers fail before native calls.

The bootstrap verdict is never used as the candidate verdict. Native validation
is separate from the adoption receipt: the October 12 operational reconstruction
was prepared locally, while only synthetic identities and policy were exercised
in the native container. Passing these tests does not validate real packets,
live identities, TLS/ACME, DERP availability, bind readiness or recovery.
In particular, `configtest` initializes state but does not execute `Serve`.

## Next live increment remains separate

Before promotion, reconcile the current ADR, re-observe configuration and
effective process/unit overrides, verify external encrypted recovery custody,
perform a restore drill, select owner and RPO/RTO, and implement a rollback that
preserves both configuration and policy. The proposed service grant and live
allow/deny matrix must be reviewed and specifically authorized.

The [private mobile IRC plan](PRIVATE-IRC-MOBILE.md) records the endpoint and
certificate gaps. Neither a new phone nor a database migration is needed to
review or test this preparation. The rsync.net account remains unavailable;
[requirements and costs](RSYNC-NET-READINESS.md) are ready for a purchasing
decision. No account was created or service purchased.
