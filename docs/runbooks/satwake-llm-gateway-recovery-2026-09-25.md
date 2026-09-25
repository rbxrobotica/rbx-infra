# Restore the Satwake generation gateway after the retired Groq model

Status: operator procedure for review, **not an authorization to sync or run a job**.

## Observed state on 2026-09-25

- The production Market Briefing CronJob is scheduled Monday-Friday at 09:00 UTC. Its jobs on 23, 24 and 25 September failed. The 25 September Loki log specifically shows `groq-test` returning `model_not_found` 404 for `llama-3.3-70b-versatile` during generation; the shell never reached publish or deliver. The exact failures on 23 and 24 were not independently logged in this check.
- Hotfix #251 is already in `rbx-infra/main`. The desired `litellm-config` maps both `groq-test` and `groq-prod` to `groq/openai/gpt-oss-120b`. The live ConfigMap still maps both aliases to the old model.
- ArgoCD Application `llm-gateway` is `OutOfSync`: `ConfigMap/litellm-config` and `Namespace/llm-gateway` differ. The Application has no automated sync. The LiteLLM Deployment is currently 1/1 ready, but readiness does not prove either model call works.
- The Deployment mounts `proxy_config.yaml` from the ConfigMap with `subPath`; [Kubernetes documents](https://kubernetes.io/docs/concepts/storage/volumes/#using-subpath) that an existing pod will not see a ConfigMap update through this mount. The Deployment has one replica and `Recreate` strategy. Restarting it creates a planned interruption of both site chat (`groq-prod`) and Briefing (`groq-test`) until the new pod is Ready.

The [Groq deprecation table](https://console.groq.com/docs/deprecations) describes an August 2026 shutdown of the old model for free/developer accounts and recommends `openai/gpt-oss-120b`. The observed 404 proves the old model is unavailable to this account. The new model still needs an account-level smoke test after rollout.

## Controlled recovery, only after specific deploy authorization

1. Confirm the active Kubernetes context, that the ArgoCD Application targets `rbx-infra/main`, and that the desired ConfigMap still differs **only** in the two model mappings and explanatory comments. Check for a concurrent Argo operation or a newer approved model change. Do not print provider credentials.
2. Choose an operator window for the one-replica outage and a check for the site chat's return. In ArgoCD, sync **only** `ConfigMap/llm-gateway/litellm-config` from the reviewed `main` revision. Do not include the unrelated namespace drift in this recovery action. Verify that the live ConfigMap now maps both aliases to `groq/openai/gpt-oss-120b`. The Application may remain globally `OutOfSync` because of the namespace; use the ConfigMap resource's own sync status as the gate.
3. Restart only `deployment/litellm` in `llm-gateway`, then wait for 1/1 ready and check the site chat returns. Verify the new pod actually mounted the current config; a healthy old pod or updated ConfigMap alone is insufficient. If sync or rollout fails, stop and diagnose rather than running a briefing.
4. With an approved, non-sensitive minimal request through the gateway, check `groq-test` and `groq-prod` separately. Record provider status without logging keys or full prompts. Confirm the provider account can use the replacement model; GitOps configuration and public model documentation alone do not prove this.
5. **Separate approval required:** run generation for one exact date in a controlled job, inspect its artifacts and manifest, then separately authorize publishing and delivery. The existing CronJob command chains `run`, `publish` and `deliver`, so do not trigger it unchanged as a smoke test. Reconcile any missed editions and subscriber notices before claiming timely service.

Record the Argo revision, ConfigMap resource version, new pod UID, probe outcomes and any edition publication IDs in the Satwake execution checkpoint. Do not count a generated test edition, test purchase or manual send as a commercial acquisition.

## Rollback decision

The previous model is unavailable to this account, so reverting to it is not a service recovery. If the replacement fails, keep publication stopped and choose a provider/model with explicit account access and content-quality review. Any further ConfigMap change and pod restart need their own authorization.
