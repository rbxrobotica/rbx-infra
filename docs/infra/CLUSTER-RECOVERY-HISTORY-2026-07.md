# Cluster recovery history for July 2026

Historical snapshot, curated on 2026-10-29. The private source is
`thinkcentre:/home/psyctl/docs/k3s-ha-cluster-state.md`, whose hash is in the
[source manifest](evidence/thinkcentre-backup-sources-2026-10-29.json).

The source records a June SQLite-to-embedded-etcd migration with three servers
and Jaguar as agent/database host, snapshot sync from Tiger to Jaguar, and
separate July robustness work. It describes sequential upgrade windows on
18, 18/19, 20 and 22 July, ending at `v1.36.2+k3s1` on the four nodes then
listed. Release-support and EOL statements belong to that snapshot and are not
current support advice.

The [upgrade runbook](../runbooks/K3S-UPGRADE-P1.md) records those four completed
steps. The isolated [etcd](evidence/2026-07-18-etcd-restore.md) and
[Thalamus](evidence/2026-07-18-jaguar-thalamus-restore.md) reports describe the
historical recovery gates. Neither proves current recovery of all state.

The source's 25 July follow-up says overnight monitoring was session-bound and
had not been rearmed for the final version. It also contains instructions to
clean an old operator worktree. Preserve those as historical context; do not
perform checkout cleanup or activate monitoring merely by following the note.

The [8 July plan](P1-CLUSTER-ROBUSTNESS-2026-07-08.md) is an earlier evidence cut.
Its unavailable-metrics and other incident statements must not be resurrected as
current findings without measurement. The [18 October reliability execution](RBX-INFRA-RELIABILITY-EXECUTION-2026-10-18.md)
is a later, separately scoped source. It records five nodes and limited recovery
measurements while leaving external ingress, capacity, N-1 and state recovery
acceptance open. July upgrade completion does not close those items.
