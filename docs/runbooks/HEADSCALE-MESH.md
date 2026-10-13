# Runbook: Headscale mesh

The legacy `bootstrap/ansible/headscale.yml` rollout is quarantined. Its two
roles now refuse execution before package, configuration, firewall or enrollment
changes. This also applies to check mode. There is no bypass variable.

The October 12 inspection confirmed that the deployed control plane uses an
explicit file-backed policy and public DERP relays. The legacy template omits
the policy and enables embedded DERP. Reapplying it is not a reconciliation.
The later rollout recovered from ThinkCentre also needs review: its initial
control-plane task overwrites the policy with an empty grant list.

Use [Headscale reconciliation](HEADSCALE-RECONCILIATION.md) for the read-only
audit, evidence, preservation requirements and the next implementation gate.
Historical deployment instructions remain available in Git history; they are
not the current enrollment procedure.

Keep the existing SSH recovery paths, DNS behavior and current mesh unchanged.
Do not enroll the workstation, Jaguar or a phone until the intended identity,
service-specific permission, positive/negative traffic matrix and rollback are
reviewed. The deployed POCO rule is a probe on TCP/18080, not IRC access.

The original [ADR-0011](../adr/ADR-0011-headscale-sovereign-mesh-on-eagle.md)
remains a historical decision. Its DERP-only sovereignty claim does not describe
the observed deployment. Importing the subsequent decision and its provenance,
with a non-conflicting ADR identifier, is part of reconciliation.
