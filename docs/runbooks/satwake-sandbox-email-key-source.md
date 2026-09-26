# Satwake sandbox email key source

Status: code-only preparation. No key has been generated or provisioned by this change. **Obtain specific operator approval before generating either key, invoking the script, or changing a Kubernetes Secret.** Approval to merge this PR does not authorize those operations.

The two credentials originate from separate, sandbox-only `pass` entries:

| Source `pass` entry | Source Secret property | Intended consumer |
| --- | --- | --- |
| `rbx/commerce-sandbox/satwake-email-sink-service-key` | `COMMS_SERVICE_API_KEY` | Commerce sandbox and sink service authentication |
| `rbx/commerce-sandbox/satwake-email-sink-operator-key` | `SATWAKE_EMAIL_SINK_OPERATOR_KEY` | Sink operator-only read endpoint |

Each entry must hold one independently generated, lowercase 64-character hex value (32 random bytes) and an optional final newline. Never copy `rbx/comms/service-api-key` or any production Comms credential into these entries. Generate them directly into the encrypted `pass` store through stdin, without printing, clipboard use, plaintext files, or secret values in command arguments. Do not overwrite an existing entry as part of this staging procedure.

## Preconditions and controlled operation

1. Confirm the reviewed kubeconfig points to the intended cluster. Confirm Infra #306's source `rbx-ia-br/rbx-commerce-sandbox-secrets` has the inert `COMMS_API_URL` and the **running Commerce sandbox pod** effectively uses `http://127.0.0.1:1`. Keep checkout quiescent. The script checks the source value; an operator must separately verify the pod environment because an old pod does not absorb Secret updates.
2. Inspect **key names only** in the source Secret. Neither `COMMS_SERVICE_API_KEY` nor `SATWAKE_EMAIL_SINK_OPERATOR_KEY` may already exist. If either exists, stop for a separate reconciliation or rotation plan; this script refuses both.
3. After specific approval, create the two dedicated `pass` entries without revealing their values. Verify their existence without `pass show` output. Run `scripts/stage-satwake-sandbox-email-keys.py /path/to/reviewed-kubeconfig` under the approved operation.
4. Verify only the two new property names and continued presence of the existing source properties. Do not print, decode, compare, or log values. A failed run may mean another actor changed the Secret; recheck metadata and key names before deciding on a new attempt.

The script reads the source Secret internally, checks its name, namespace, inert URL, absent key names and `resourceVersion`, then reads both dedicated `pass` entries. It rejects malformed or identical keys. It sends one atomic Kubernetes JSON Patch through stdin with a `resourceVersion` test and two `add` operations. Concurrent Secret changes fail the test; other properties are preserved. Command output and diagnostics are suppressed so values cannot appear in terminal output. The patch does not change an ExternalSecret mapping or pod environment.

Infra #307's separate ExternalSecrets may be merged only after their own approval and verification of these source properties. Confirm both targets Ready and their expected **key names** before deploying the sink workload in Infra #304. Keep the Commerce URL inert until a separately approved sink cutover. The operator key must never be copied into the Commerce runtime Secret or pod. If the source Secret is recreated from scratch, these additional keys must be deliberately re-provisioned under a new approval; the Ansible role only preserves them when the Secret already exists.

If staging or a later rollout fails, keep the Commerce URL inert and stop. Do not use a broad `k8s-secrets` run, an automatic delete, or a production credential as rollback. Removal or rotation of an issued key needs a separately reviewed operation.
