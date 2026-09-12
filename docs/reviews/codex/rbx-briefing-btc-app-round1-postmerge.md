# Codex review: codex-infra-post-merge (post-merge / first review)

- Date: 2026-09-12
- Reviewer: gpt-5.3-codex-spark, medium, codex exec --ephemeral -s workspace-write
- Branch: main (HEAD ab5c164)

## Reviewer output

Findings
- No blocking findings in the merged change set.

Checks run
- `kubectl kustomize apps/prod/rbx-briefing-btc` (success).
  - Confirms: `rbx-briefing-btc` and `rbx-briefing-btc-session-bff` render with image pins `sha-df48c53` and `sha-515765f` respectively; `IngressRoute` matches `/briefing-btc` + `/briefing-btc/` only; `app.merovelis.com` paths do not overlap Strategos’ `/strategos`/`/api/auth` rules.
- `kubectl kustomize apps/prod/rbx-commerce` (success).
  - Confirms: added `renewals-cronjob.yml` renders cleanly and BTCPay/Payrexx env/ref wiring is present.
- `yamllint` on the changed files (fails).
  - `docs/infra/SECRETS.md:3` (`*` not valid YAML at top-level), plus multiple long-line violations.
  - `scripts/check-image-convention.sh:32` (YAML parser error on shell file).
- `ansible-lint --offline` on `bootstrap/ansible/roles/k8s-secrets` (fails, 236 violations).
  - Failures are largely repository-wide style/deprecation rules (`role-name`, `fqcn[action-core]`, `risky-shell-pipe`, `no-changed-when`) and are not tied to the new rbx-briefing-btc additions.

Routing/security/secret/RBAC review highlights
- No route precedence conflict detected between `app.merovelis.com/briefing-btc` and existing Strategos routes.
- CSRF/session cookie scope is separated by distinct names (`__Host-rbx_briefing_*`) despite shared host.
- Required new RBAC path/mirrors are present in `apps/prod/rbx-ia-br/rbac-external-secrets-reader.yml` and corresponding `ExternalSecret` resources.
- The session-BFF annotate-mode concern is addressed: the merged manifest uses `apps/prod/rbx-briefing-btc/kustomization.yml:33` (`sha-515765f`), not `sha-ea0329c`, so no stale annotate-mode tag mismatch is present.

VERDICT: APPROVE
