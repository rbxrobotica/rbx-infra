# Tasks

Status: backlog; every delivery task is pending. Creating this card does not
authorize execution of deployment operations.

## 1. Architecture and delivery contract

- [ ] 1.1 Select app/build owner, upstream pin and patch strategy, cloud environment, internal audience, hostname and capacity/cost envelope; confirm Atlas navigation versus Portal scope and document alternatives/trade-offs.
- [ ] 1.2 Write design and delta specs for IAM, per-repository workspace isolation, Git/PR permissions, concurrency, persistence, backup/restore, failure recovery and query/resource budgets.

## 2. Cloud preparation

- [ ] 2.1 Prepare the application image and scoped configuration with reproducible inputs, security checks and providers/Specflow disabled by default.
- [ ] 2.2 Implement RBX Identity integration and workspace/file/process boundaries; verify unauthenticated, cross-workspace, path-escape and unauthorized-command rejection.
- [ ] 2.3 Prepare GitOps manifests, external secret references, persistent storage, network/resource constraints, probes, observability and rollout/rollback runbooks without activating production.

## 3. Acceptance and frontend discovery

- [ ] 3.1 Verify login, authorized repository access, card creation/editing, conflict handling, repeat/restart behavior and backup/restore in an isolated environment; retain evidence and unresolved prerequisites.
- [ ] 3.2 Add a correctly scoped Atlas entry to Studio's authenticated frontend, honoring Atlas exposure/auth state and keeping private metadata out of the public build; verify navigation and authorization boundaries.
- [ ] 3.3 Present the concrete rollout and recovery plan for deployment authorization; after authorization, perform the scoped delivery and capture live smoke evidence, resource/cost observations and handoff.
