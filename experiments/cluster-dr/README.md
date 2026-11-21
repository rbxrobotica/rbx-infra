# Second-cluster observation and offline sizing

This experiment supports the 2026-11-16 provider-loss study. It does not deploy,
order capacity, bootstrap a second cluster, restore data, or qualify failover.
Recovery evidence and the business/provider proposal belong in `rbx-resilience`,
`evidence/2026-11-16-cluster-dr/` and
`docs/experiments/2026-11-16-second-cluster-sizing-and-provider-comparison.md`.

## Read boundaries

- `observe.py`: fixed Kubernetes API server and expected `kube-system` UID,
  checked before and after collection. Only inventory/metrics GETs; bounded
  subprocess output and timeout. Serializers exclude annotations, environment,
  commands, secret references, Helm values, logs and object payloads.
- `history.py`: same identity guard, fixed Prometheus service proxy GETs and an
  allowlist of namespace aggregates. Seven-day lookback uses **hourly** samples;
  sample count and source retention accompany the values. This is not a
  full-resolution peak, complete seven-day history or service SLO. API proxy
  access is privileged: use only the already authorized context.
- `postgres_observe.py`: execute only on the reviewed PostgreSQL host under the
  existing trusted access path. Uses local Unix socket, `psql -X -w`, clean PG
  environment, read-only default transaction, bounded SQL/lock/process timeouts.
  Fixed catalog/settings/size/extension queries only. No table rows, credentials,
  application SQL, dumps, maintenance, replication or configuration changes.
- `volume_metadata.py`: execute only for explicit reviewed PV-to-host mappings,
  not a filesystem-wide crawl. Validates existing nonsymlink local-path roots;
  `du -s -x -B1` counts allocated blocks without opening file contents. Uses
  idle I/O/low CPU priority when available and a 15-second per-volume timeout.
  Failed measurements remain gaps. Live allocated blocks are not a consistent
  database backup, logical bytes, restored size or enforced PVC quota.
- `analyze.py`: offline only. Reads two sanitized receipts, identifies missing
  pod metrics and produces namespace/profile budgets with explicit assumptions.
  It cannot establish capacity fit, priority, recovery order or backup health.

Existing access credentials are neither exported nor copied. SSH collection used
workstation -> ThinkCentre -> preexisting root host aliases, strict host-key
verification, batch mode, no forwarding and resolved hostname/user pin checks.
No credential files, state contents, application tables or file payloads belong
in this evidence. The root executor keeps host/PV mappings in memory; serialized
volume receipts retain a path hash rather than the actual path.

## Reproduction

Review current authorization, identity pins and the state/recovery preflight
before any live read. This tool is tied to one source cluster; do not replace
its UID to enroll an unrelated cluster automatically. A read failure is not an
empty cluster or zero demand. Hash each exact helper **before** execution and
keep its source unchanged until collection finishes; preserve errors and prior
receipts separately. Collections are not a transactional snapshot.

With the existing authorized kubeconfig and the expected server:

```sh
python3 -B experiments/cluster-dr/observe.py
python3 -B experiments/cluster-dr/history.py
```

Capture stdout to a private evidence destination, adding the pre-execution
source hash without exposing stderr or credentials. For host helpers, review
the named host and script and use the existing SSH path; do not run on the
workstation and misattribute its result to Jaguar. The volume helper accepts a
JSON list on stdin with `pv`, `namespace`, `claim`, `node` and exact `path`.
Build that mapping from `.spec.local.path`/node affinity and validate it against
the resolved SSH alias. Never infer a path from just a claim name or ignore a
failed host identity check. Host helpers only need Python and existing system
tools; no installation, sudo policy change or service restart is part of this
experiment.

Offline worksheet regeneration, using the captured resilience directory:

```sh
python3 -B experiments/cluster-dr/analyze.py /path/to/2026-11-16-cluster-dr
python3 -B -m unittest discover -s experiments/cluster-dr -p 'test_*.py' -v
```

The worksheet uses the greater of requests, current known usage and sampled
namespace maxima, adds a proposed 0.5-GiB uncertainty allowance, 30% application
margin and 2-GiB OS/k3s reserve. These are hypotheses for a target rehearsal,
not measured overhead or proof that a vendor's advertised GB will fit. Node
layout changes DaemonSets, anti-affinity, rollout surge and allocatable CPU/RAM.
External PostgreSQL, restored data, WAL, image layers and backup workspace need
separate budgets. The observed fleet includes dev/test/held jobs and is not an
approved recovery scope.

The offline tests prohibit real subprocess/network calls, use synthetic secret
sentinels and temporary files, and check identity refusal, payload omission,
effective resource requests, incomplete metrics, PostgreSQL connection isolation,
historical result validation, local-volume path/DU bounds and sizing uncertainty.
CI executes only those offline tests, without cluster credentials.

## Current limitations

At the November 16 capture, the seven-day query yielded about 47 hourly points
for principal namespaces. One Langfuse ClickHouse directory remained unmeasured
after two bounded attempts. PVC metrics described backing filesystems, not
individual claim usage. No throughput, sustained CPU, fsync latency, traffic,
customer criticality, production restore or whole-provider-loss test was executed.
No scheduling overlay or purchasing decision follows from the measurements.
