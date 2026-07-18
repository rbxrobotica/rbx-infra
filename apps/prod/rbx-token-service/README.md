# rbx-identity — token service (Gate A pilot)

`rbx-token-service`: opaque delegated session credentials (ADR-0101 first
slice) with `/v1/delegation/sessions|introspect|revoke` behind
`RBX_TOKEN_SERVICE_DELEGATION=on`. Pilot registry: Leandro (active,
kulinaryos) and Rafael (`pending_identity`).

**No public ingress.** Introspection carries no credential of its own in this
slice; NetworkPolicy restricts ingress to the `thalamus` namespace.

## Caveats

- Registry is **in-memory**: a pod restart revokes all outstanding
  credentials (TTL is 20 min by default anyway). Persistence arrives with the
  ZITADEL token-exchange swap, which replaces issuance without touching the
  Thalamus session model.

## Issue a credential (operator)

```bash
kubectl -n rbx-identity port-forward svc/rbx-token-service 8082 &
curl -s -X POST http://127.0.0.1:8082/v1/delegation/sessions \
  -H 'content-type: application/json' \
  -d '{"subject":"ldamasio@gmail.com","audience":"thalamus","scopes":["kulinaryos:access"],"client_app_id":"operator-cli"}'
# → { "opaque_session_credential": "...", "session_id", "jti", "expires_at", ... }
# present opaque_session_credential as Bearer to thalamus /rbx/v1/*
```

## Sync order

Sync this app **before** flipping `THALAMUS_RBX_API=on` in `apps/prod/thalamus`
— the Thalamus `/readyz` probes the introspection endpoint and goes unready if
it is unreachable.
