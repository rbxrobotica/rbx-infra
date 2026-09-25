# Prepare the Commerce→Comms service key

The Satwake email-code endpoint is authenticated with the Comms service key.
Commerce needs a copy of the already configured key in its source Secret
`rbx-ia-br/rbx-commerce-secrets`. The [patch script](../../scripts/patch-commerce-comms-key.sh)
changes only `data.COMMS_SERVICE_API_KEY` on that Secret. It does not create a
new credential, print its value, or touch `rbx-contact-secrets`.

This is a **production secret change** and needs specific operator approval.
The script is a prepared operation; merging this documentation does not run it.
Do not use the broad `k8s-secrets` role for this step: its current contact
task still omits the three Meta credentials required by the active Comms
deployment. The owner has replaced D360 account operations with the Meta
WhatsApp Business MCP; Comms' automated WhatsApp runtime still uses its Meta
Graph sender.

Before running the script, inspect the kubeconfig context and confirm it
targets the intended production cluster. Confirm that the `pass` entry
`rbx/comms/service-api-key` is present and that Commerce and Comms currently
have healthy pods. The `pass` entry must contain exactly one nonempty line;
the script rejects a multiline entry rather than silently selecting a value
different from the one configured for Comms. After specific approval, run:

```sh
scripts/patch-commerce-comms-key.sh ~/.kube/config-rbx
```

Verify by checking **presence only** of the new key in the source Secret;
do not print or decode its value. Also confirm the existing
`rbx-ia-br/rbx-contact-secrets` still contains `META_ACCESS_TOKEN`,
`META_APP_SECRET`, and `META_WEBHOOK_VERIFY_TOKEN` and that Comms remains
healthy. Then the separate mapping PR may be merged under deployment
approval. ArgoCD auto-syncs Commerce, so this source preparation must happen
**before** that merge; otherwise the mandatory env reference can stall the
Commerce rollout.

This narrow patch is temporary until the `k8s-secrets` role can safely
provision the active Meta credentials and this Commerce property together.
If `rbx-ia-br/rbx-commerce-secrets` is recreated or restored, rerun the
approved patch before allowing a Commerce rollout; watch the Commerce
ExternalSecret readiness and pod readiness. Do not assume the source property
survives a Secret recreation.
