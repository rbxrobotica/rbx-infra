# rbx-ledger (staging)

Internal finance operations console (Rust backend + SvelteKit frontend).
rbx-ledger is an **isolated financial domain**: dedicated namespace, secret,
PostgreSQL database and role. It does **not** share secrets through rbx-ia-br.

## Out-of-band prerequisites (not in git)

Provisioned manually in the `rbx-ledger` namespace before ArgoCD sync:

- `rbx-ledger-secrets` — key `DATABASE_URL` (dedicated staging DB/role on the
  official RBX PostgreSQL). The backend reads only `DATABASE_URL`.
- `ghcr-pull-secret` — dockerconfigjson for pulling the private GHCR images
  (referenced by both Deployments via `imagePullSecrets`).

Passwords are kept in `pass` on the operator workstation, never in git.

## Migrations

Run automatically by the backend on startup (`RBX_LEDGER_RUN_MIGRATIONS=true`,
embedded `sqlx::migrate!`). No separate migration Job is required. The DB role
must own its database so it can create the schema.
