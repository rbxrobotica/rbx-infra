# Read-only IRC capacity qualification

This metadata probe is an operator-run diagnostic, not a deployed agent or
continuous monitor. It installs no package, unit, timer, credential or container.
It never opens IRC databases, configuration contents, logs or conversation text.
Use existing verified SSH access with no agent forwarding and no host-key bypass.

Run the public Python program through stdin on the actual IRC host, using its
existing Docker operator identity. No sudo is required when Docker and cgroup
metadata are available to that identity:

```sh
python3 -B capacity_probe.py --samples 6 --interval 10
```

Samples are sequential **per container**, not simultaneous. The default covers
Ergo and separately ZNC; it does not move them together. Six samples span about
50 seconds plus query overhead for each running container. Values include cgroup
memory/cache/kernel usage, lifetime cgroup peak/events, single-core CPU counters,
namespace network counters and established TCP counts. These are not
authenticated-user counts, workload transactions, daily peaks or service SLOs.
Container generation is checked before every sample. Docker stats provides a
bounded fallback where host `/proc` is unavailable. Missing permissions are
reported as incomplete, never interpreted as zero state or a valid backup.

`--container-metadata` additionally starts a fixed, brief `du`/`awk` helper inside
the existing **Ergo** container after the timed samples. It reports allocated
KiB for `/ircd` and aggregate established IRC TCP connections, without opening
payloads or changing the service. This helper is a separate observation, not a
sample of the same instant. The flag does not run it in ZNC. No arbitrary path
or shell program can be supplied through the CLI.

Filesystem metadata uses a fixed declared host root, skips symlinks, limits
directory/file enumeration, and reports access errors and excluded mounts.
Logical file sizes, allocated space, retained payload volume and restore working
space are different measurements. Backup directory metadata does not prove
consistency, encryption, independent custody or a successful restore.

Strategos is a separate systemd unit; desktop adapters/mailboxes and the proposed
PostgreSQL mailbox are outside this container probe. Do not call mailbox CLI
`status` as a strict read-only substitute: it may expire stored state.

The November 16 qualification used the existing ThinkCentre-to-Corbetti SSH path.
An initial sudo prerequisite failed; a second unprivileged attempt reported
incomplete mount access. The revised six-sample run obtained cgroup data and
Ergo container metadata, retaining the errors separately. Its ZNC helper used
the Ergo-only path and failed without invalidating its cgroup samples; the final
program restricts the helper to Ergo and removes the assumed bot container.
This is not evidence of a ZNC data fault or a production configuration change.
Sanitized observations and the long-term comparison are in resilience PR 11.

Offline safety checks:

```sh
python3 -B -m unittest discover -s experiments/irc-capacity -p 'test_capacity_probe.py' -v
```

They forbid real subprocess execution and check payload privacy, external
symlinks, inaccessible/incomplete metadata, enumeration bounds, selective Docker
fields, generation changes and explicit helper/fallback behavior. They do not
qualify a provider, backup or production resource minimum.
