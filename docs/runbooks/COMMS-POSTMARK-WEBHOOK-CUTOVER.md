# Dedicated Comms Postmark webhook credential

Prepared configuration, not evidence of provisioning or provider cutover. The
2026-10-05 read-only inspection found the edge BasicAuth Secret absent and no
application references to the new webhook username/password. API main verifies
all three Postmark callbacks with dedicated BasicAuth; its outbound API token
must not be reused as that credential.

## Fixed scope

| Location | Contents |
| --- | --- |
| `pass`: `rbx/comms/postmark-webhook-auth` | One encrypted JSON bundle: `username`, `password` |
| Source Secret: `rbx-ia-br/comms-postmark-webhook-auth` | `username`, `password`, bcrypt `htpasswd` |
| Application Secret: `rbx-comms/comms-postmark-webhook-auth` | ESO projects only `username`, `password` |
| Edge Secret: `rbx-comms/comms-webhook-basic-auth-postmark` | ESO projects `htpasswd` as `users` |

Only `rbx-comms/external-secrets-reader` receives `get` on the dedicated source
Secret. Kubernetes grants access to the whole named Secret, not individual keys.
The shared contact Secret, Meta credentials and outbound Postmark token are not
modified. Do not run the broad `k8s-secrets` role for this operation.

## Preparation and source provisioning

The following are operational commands for the separately authorized cutover;
they are not validation commands and must not run while preparing a PR.
Prerequisites: a reviewed private kubeconfig, unlocked GPG/pass, `kubectl`, Python
3, and Apache `htpasswd` with bcrypt support. The operator prepared Apache `htpasswd` in a private temporary directory
from Ubuntu packages, without installing system packages. No credential
generation or staging is implied by preparing this change.

From the repository root, after authorization to create this dedicated pair:

```sh
python3 scripts/stage-comms-postmark-webhook-auth.py generate
ansible-playbook -i localhost, bootstrap/ansible/stage-comms-postmark-webhook-only.yml \
  -e comms_webhook_kubeconfig=/path/to/reviewed-private-kubeconfig
```

The first command creates one new encrypted pass entry. It prints no value and
refuses an existing entry. The standalone playbook uses `no_log: true` and invokes
only the source-Secret provisioner. It does not import `site.yml` or any broad
secret role. `--check` skips provisioning; `--syntax-check` performs no operation.

The script uses a 384-bit random password, bcrypt cost 12, captured stdout and
stdin payloads. No credential is placed in process arguments or plaintext files.
The Kubernetes operation is create-only: an existing source or concurrent create
fails without overwriting anything. If a command fails after generating the pass
entry, preserve that entry and resolve the specific failed step; do not regenerate
the pair. A timed-out create has an unknown outcome: check only Secret metadata
and expected key names before retrying. The script deliberately offers no rotate,
delete, force or overwrite option.

## GitOps and provider cutover

Read-only provider metadata on 2026-10-05 found server `19089132` pointing its
inbound URL and all three existing outbound-stream hooks to the UI host
`comms.rbxsystems.ch`, without BasicAuth or custom headers. The API ingress uses
`api.comms.rbxsystems.ch`; URL correction and dedicated authentication must be
cut over together. Update these existing hooks in place; do not create duplicates:

| Existing webhook ID | Preserved trigger | Preserved path |
| --- | --- | --- |
| `24453184` | Bounce only | `/api/webhooks/postmark/bounce` |
| `24453185` | SpamComplaint only | `/api/webhooks/postmark/bounce` |
| `24453186` | Delivery only | `/api/webhooks/postmark/delivery` |

Keep each stream, all trigger flags and any other callback settings unchanged.
The server inbound path remains `/api/webhooks/postmark/inbound`. Capture a fresh
sanitized metadata snapshot immediately before the operation and stop on drift;
this historical inventory is not authority to overwrite newer provider settings.

1. Confirm the reviewed application image implements the dedicated BasicAuth
   contract and is published. Secret preparation alone does not repair the old
   image's custom-header requirement for inbound callbacks.
2. Reconcile the source Role/RoleBinding in the `rbx-ia-br` application, then the
   two ExternalSecrets in `rbx-comms`. Verify both are `Ready=True` and check only
   destination Secret type/key names. An Opaque edge Secret must contain `users`.
3. Verify the unauthenticated external Postmark route now returns 401. Retain the
   existing middleware and its Authorization header (`removeHeader=false`).
4. Reconcile the reviewed API image and its two required Secret references:
   `POSTMARK_WEBHOOK_USERNAME` → `comms-postmark-webhook-auth/username`,
   `POSTMARK_WEBHOOK_PASSWORD` → `comms-postmark-webhook-auth/password`. Confirm
   both replicas are ready and unauthenticated internal callbacks reject access.
5. Under the authorized provider configuration operation, use the same encrypted
   bundle to change the existing host to `api.comms.rbxsystems.ch`, set inbound
   URL BasicAuth and stream webhook `HttpAuth`, preserving the paths and existing
   webhook IDs above. Preserve trigger flags and other settings; do not create
   additional webhooks. Remove an obsolete stream custom API-token header only
   if a fresh inventory finds it; the inspected hooks had no custom headers.
   Build credential-bearing URLs/JSON and request headers in memory;
   never put them in shell arguments, logs, PRs or browser screenshots.
6. Verify a separately approved synthetic callback produces one persisted result
   and inspect provider retries. Negative probes and readiness do not prove real
   callback delivery or absence of lost events. Record the maintenance interval.

The value-free provider helper implements this exact inventory and stops on
server, hook, stream, trigger, header or credential drift:

```sh
# Read-only plan; requires only the existing institutional server token.
python3 scripts/cutover-comms-postmark-webhooks.py
# Only after steps 1–4: preserves IDs/triggers, changes URL/auth, verifies readback.
python3 scripts/cutover-comms-postmark-webhooks.py --apply
```

Provider webhook verification is requested as part of the update; it can send
synthetic callback events, but does not send an email. These callbacks are not
proof of a real message reaching the founder inbox. A timeout means unknown
outcome: inspect the sanitized provider inventory before deciding on a retry.

Keep the provider token and endpoint configuration unchanged while preparing this
artifact. No provider mutation is implemented by the source provisioner. Rolling
back just the image restores the old custom-header requirement and does not fix
inbound; callback rollback requires coordinated provider/application handling.
Never remove authentication to recover availability.
