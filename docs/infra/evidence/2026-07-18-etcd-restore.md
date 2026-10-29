# Historical isolated etcd restore evidence

Event date: 2026-07-18. Curated on 2026-10-29 from the source identified in the
[manifest](thinkcentre-backup-sources-2026-10-29.json). This is a retrospective
summary, not a new drill or a copy of the production snapshot.

The source reports snapshot integrity verification, restoration to a new data
directory with membership reduced to one isolated member, successful etcd boot,
and read checks in a workstation container. Snapshot/live resource counts were
reported as forty-one namespaces, 110 pods and ninety-three Service specs on
both sides. The distinction between Service specs and legacy Endpoints matters
for reproducing that comparison. Production was read for the snapshot copy;
no production restore or quorum membership change was reported.

The source labels the isolated attempt PASS. Its snapshot/data directory and
container were reported removed after the test. A matching document hash today
does not prove retention of the original snapshot or logs.

The original report still says the PostgreSQL drill was pending. The separate
[Thalamus report](2026-07-18-jaguar-thalamus-restore.md) of the same event date
records a successful rerun. Preserve both original statements and cite that
reconciliation rather than rewriting either attempt's outcome. Modification
times alone cannot determine execution order.

This test does not prove rebuilding the three-member production quorum,
restoring node-local PVCs, workload readiness, outside-provider recovery or
present-day readiness. No complete observed RPO/RTO or formal Resilience drill
execution is reconstructed from these notes.
