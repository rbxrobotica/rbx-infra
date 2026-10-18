# Synthetic Headscale native validation

These fixtures are test inputs, not network policy proposals. `admin` and `phone`
are synthetic identities. TCP/18080 is a synthetic service probe, not an IRC
endpoint. No permission for IRC, database access or production SSH is created.

Run from a checkout with rootless Podman, Python 3, PyYAML, Jinja2, `dpkg-deb`,
`sha256sum` and `timeout` installed:

```sh
scripts/network/test-headscale-candidate-native.sh
# Or reuse the official release archive; the same SHA-256 check is mandatory:
scripts/network/test-headscale-candidate-native.sh /path/to/headscale_0.29.3_linux_amd64.deb
```

The harness downloads and verifies the official Headscale 0.29.3 Debian archive
without installing it, and uses an Ubuntu container pinned by digest. It requires
Linux/amd64 and a non-root caller. It never accepts configuration or policy files
from the caller. The only inputs are synthetic fixtures and the repository's
candidate template/defaults. It invokes the actual offline preparer, retaining
config and policy bytes in a private bundle. Missing files, empty files, absent
rules and empty grants must be rejected before native execution.

The native sandbox has no network, published ports, capabilities, host home,
production state, SSH material or container-engine socket. Its root filesystem
and three bind mounts are read-only. SQLite, generated keys, runtime sockets and
logs live only in bounded tmpfs. PID, memory, CPU, file-descriptor and elapsed-time
limits apply. The process environment is cleared before executing the runner.
Every Podman invocation forces `--remote=false`; environment variables or saved
remote connections cannot redirect validation or cleanup to another host.
The host-side Python runs in isolated mode, so `PYTHONOPTIMIZE` cannot disable
validation assertions.

A separate deny-all bootstrap config starts a daemon inside the isolated
container only to seed two synthetic users and three synthetic nodes via the
native CLI. The daemon stops before any direct SQLite access. Its bootstrap
config is never the candidate verdict. The complete prepared config then runs
through native `configtest` without changing any field or path. The report links
the rendered candidate, preserved current bytes and executed bundle hashes.

Native `policy check` evaluates two accepted and six denied TCP flows against
the synthetic identities and nodes. Three additional checks must reject an
invalid policy schema, a test source referring to a missing identity, and swapped
port grants with unchanged expectations. Unknown usernames in grants can be
accepted by Headscale's initialization path; the explicit expected-allow test is
what makes the missing-identity case fail.

The harness does not test real packets, live identities, relay connectivity,
ACME/TLS certificate acquisition, live socket binding or production recovery.
`configtest` calls initialization, not `Serve`; success is not proof that every
runtime dependency is reachable. No deployment or live database access follows
from a passing result. Private temporary archives, logs and bundles are removed
on exit, and only a sanitized JSON report is printed.
