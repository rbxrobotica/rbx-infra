# Satwake Commerce sandbox core source properties

Status: **code-only preparation**. This change generates no values and touches no live Secret. Obtain specific operator approval before creating either `pass` entry or running the staging script. A PR merge is not approval to do either.

| Durable `pass` entry | Source Secret property | Generation |
| --- | --- | --- |
| `rbx/commerce-sandbox/altcha-secret` | `ALTCHA_SECRET` | 32 random bytes, 64 lowercase hex characters |
| `rbx/commerce-sandbox/public-tenant-id` | `COMMERCE_PUBLIC_TENANT_ID` | A newly generated canonical UUIDv4 |

Both entries belong only to the sandbox. Do not reuse the production ALTCHA key or public tenant `885f24d2-1217-534e-bb1b-53440a3c04bb`. The tenant is not confidential, but keeping its single durable copy in `pass` lets the future buyer session BFF use the **same** UUID after an independently reviewed rollout. The zero UUID would silently route checkout into the legacy/default tenant and is rejected.

## Preconditions

1. Confirm the reviewed kubeconfig and cluster. Confirm the source Secret `rbx-ia-br/rbx-commerce-sandbox-secrets` exists with `DATABASE_URL`, `COMMS_API_URL`, `ASAAS_API_KEY`, `ASAAS_WEBHOOK_TOKEN`, `COMMS_SERVICE_API_KEY`, and `SATWAKE_EMAIL_SINK_OPERATOR_KEY`. Inspect property **names only**. Neither `ALTCHA_SECRET` nor `COMMERCE_PUBLIC_TENANT_ID` may already exist; existing values need a separate reconciliation, never an overwrite by this script.
2. Confirm the source `COMMS_API_URL` and the **running sandbox Commerce API pod** both use `http://127.0.0.1:1`. Keep the sandbox checkout quiescent. The script checks the source URL; the operator must check the running pod because a Secret update does not change an existing pod's environment.
3. Confirm both dedicated `pass` entries are absent. Do not overwrite an existing entry or place either value in command arguments, shell history, clipboard, plaintext files, terminal output, or a PR.

## Controlled staging after separate approval

Generate each value directly into its own encrypted `pass` entry through stdin. For example, after verifying the entries are absent, the operator can run these commands with `set -o pipefail` and a reviewed `pass` store:

```bash
python3 -c 'import secrets; print(secrets.token_hex(32))' | pass insert -m rbx/commerce-sandbox/altcha-secret
python3 -c 'import uuid; print(uuid.uuid4())' | pass insert -m rbx/commerce-sandbox/public-tenant-id
```

Verify entry existence without displaying values. Run `scripts/stage-satwake-sandbox-core-secrets.py /path/to/reviewed-kubeconfig` only under the approved operation. The script reads both entries internally, enforces a 64-character lowercase hex ALTCHA key and a canonical UUIDv4 tenant distinct from zero and production, and checks the existing source Secret. It then sends one atomic JSON Patch through stdin: a `resourceVersion` test followed by `add` operations for just `ALTCHA_SECRET` and `COMMERCE_PUBLIC_TENANT_ID`. If the Secret changes concurrently, the patch aborts. The script suppresses Kubernetes and `pass` diagnostics that could echo values.

Afterward, verify **only property names** and that the pre-existing source properties remain present. Do not print, decode, compare, or log values. If staging fails, inspect metadata and property names before any retry; never use a broad Secret recreate as recovery.

This source-only stage does **not** mirror the two properties into `rbx-commerce-sandbox`, change a Deployment, start the email sink, activate checkout, create a buyer identity, or send external messages. Future ExternalSecret, Commerce, and buyer session BFF changes require separate review and approval. The buyer BFF must resolve the same durable sandbox tenant before a sandbox payment can prove buyer access and consumption. Keep the Comms URL inert until the isolated sink path is separately approved and verified. A later Ansible run preserves extra properties only while this source Secret exists; recreation requires deliberate re-provisioning.
