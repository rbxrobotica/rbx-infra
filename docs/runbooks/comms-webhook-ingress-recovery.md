# Comms webhook ingress recovery — 2026-09-30

Infra #198 narrowed the public API surface but omitted the deployed Meta
callback. Live inspection found both provider BasicAuth Secrets absent, and
Meta verification and the Postmark delivery URL returned HTTP 404. Argo health
alone does not validate callback routing. No lost delivery count was established.

## Meta recovery

Expose only `/api/webhooks/meta/whatsapp`, GET and POST, on both existing API
hosts. The deployed application sha-752b3db validates the GET verification token
and POST `X-Hub-Signature-256` HMAC before processing. Missing application
credentials reject requests. Do not add legacy BasicAuth to Meta or restore a
catch-all API route. The image and provider registration are unchanged.

After GitOps convergence, verify both hosts: GET without verification token
returns 403 (503 means missing configuration); unsigned POST returns 401
(503 means missing configuration); `/metrics`, `/api/contacts`, and an unknown
webhook remain 404. These negative probes do not prove a real Meta delivery.

## Postmark recovery remains gated

Keep the existing edge BasicAuth requirement. Missing Secret is not justification
for bypassing authentication. Provisioning requires a dedicated credential in
the password store and `rbx-comms/comms-webhook-basic-auth-postmark`, type Opaque,
with valid nonempty htpasswd `users`. Configure the same dedicated credential in
the provider only after the unauthenticated route returns 401.

The deployed application additionally requires `X-Postmark-Server-Token` on all
three Postmark routes. Stream delivery/bounce hooks support custom headers;
inbound only supports URL BasicAuth. Consequently, creating the edge Secret is
not sufficient for inbound: prepare application-level dedicated BasicAuth
verification with fail-closed missing configuration, tests, and coordinated
credential/provider cutover. Do not remove the application check and trust an
unverified proxy header. Do not reuse the Postmark API token as a URL password.

D360 is legacy after the Meta migration; do not create credentials or activate it
merely to satisfy the obsolete onboarding sequence. Retirement must confirm no
remaining configured consumers. No provider credential, webhook registration,
message, or financial operation is changed by the Meta route patch.
