# PI VI — Secretaria Digital preview

Public review URL: https://rbx.ia.br/estudos/historicos-escolares/

Static SvelteKit 3 / Svelte 5 frontend. No backend, database, real student data, secrets, shared user records or outgoing notifications. All personas and state are synthetic and browser-local. The requester/secretariat switch is explicitly a simulation, not authentication. Future API and persistence, if implemented, use Go and PostgreSQL.

The path uses existing RBX HTTPS and is isolated from the institutional frontend by a more-specific ingress. No DNS changes, image push, new TLS secret or site-wide rebuild. The deployment has no service-account token, runs as UID 101 with a read-only root filesystem, and mounts only the generated public artifact and nginx configuration. One replica is sufficient for this review-only preview; no availability guarantee or school deployment is claimed.

`frontend/` contains reproducible source and pinned npm lockfile. Build with Node 24, `npm ci`, `npm run check`, `npm test`, `npm run build`. `site.tgz` is the deterministic build archive; regenerate it from `frontend/build/` using `scripts/package-pi-vi-historicos.py` at the repository root.

Kustomize generates hashed configmaps. ArgoCD owns the rollout. Rollback is a revert of this application change; remove only this application's path and resources. The pre-existing institutional site's ingress and deployment are not modified.

Verification notes and the archived tests are in the task workspace; private WhatsApp messages, PDFs and student information are deliberately excluded from this repository.
