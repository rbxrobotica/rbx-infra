# Eden + RBX Catalog Implementation Guide

**Canonical guide:** this document is the cross-repository source of truth for
how Eden provisions products and how those products become visible in the RBX
runtime catalog.

**Scope:** `eden`, `rbx-infra`, `rbx-catalog-registry`, `rbx-catalog-api`,
`rbx-catalog-console`, and the CI/CD handoff between them.

Repo-local guides remain useful for implementation details inside each repo.
If they disagree with this document on ownership, lifecycle, or end-to-end
flow, this document wins.

---

## 1. System Boundary

The system has five distinct responsibilities:

| Repository | Responsibility | Owns | Does not own |
|---|---|---|---|
| `eden` | Product provisioning automation | CLI flow, scaffolding orchestration, catalog registration calls | Long-lived runtime state, cluster manifests after generation |
| `rbx-infra` | GitOps and production deployment state | Kubernetes manifests, Argo CD apps, namespaces, image tags, legacy portfolio catalog | App source code, runtime catalog taxonomy |
| `rbx-catalog-registry` | Runtime catalog source of truth | YAML entities, schema reference, taxonomy docs | API service code, UI code, deployment manifests |
| `rbx-catalog-api` | Read-only serving layer | FastAPI loader, Pydantic validation, endpoints, API image | Catalog entity ownership, UI logic |
| `rbx-catalog-console` | Human-facing catalog browser | Next.js UI, typed API client, console image | Registry mutation, API write logic |

The important distinction:

- `rbx-infra/catalog/products.yml` is the **legacy Eden portfolio catalog**.
  It keeps product metadata that Eden still needs: phase, namespace, domains,
  repo, owner, created date, and description.
- `rbx-catalog-registry/catalog/**` is the **runtime catalog** consumed by
  `rbx-catalog-api` and `rbx-catalog-console`.

Do not make `rbx-catalog-api` read `rbx-infra/catalog/products.yml`. Runtime
visibility must flow through `rbx-catalog-registry`.

---

## 2. Primary Workflow: `eden new`

The normal path for creating a new RBX product is Eden.

```bash
eden new my-api --type=api --domain=my-api.rbx.ia.br --catalog-domain=platform
```

End-to-end flow:

1. Eden loads `~/.eden.yml`.
2. Eden collects product inputs from flags or prompts.
3. Eden generates Kubernetes manifests in `rbx-infra/apps/prod/<name>/`.
4. Eden generates an Argo CD Application in
   `rbx-infra/gitops/app-of-apps/<name>.yml`.
5. Eden adds the namespace to
   `rbx-infra/gitops/projects/rbx-applications.yaml`.
6. Eden writes the legacy portfolio record to
   `rbx-infra/catalog/products.yml`.
7. Eden writes the runtime entity to
   `rbx-catalog-registry/catalog/<entity-type>/<name>.yaml`.
8. Eden commits and pushes `rbx-infra` to `main`.
9. Eden commits and pushes `rbx-catalog-registry` to its current branch.
   For production visibility, run Eden from the registry branch that triggers
   CI/CD. The current workflow watches `main`.
10. Eden applies the Argo CD Application with `kubectl apply`.
11. Argo CD syncs the product manifests from `rbx-infra`.
12. The registry CI dispatches `registry-updated` to `rbx-catalog-api`.
13. The API CI rebuilds an image with the registry snapshot baked in.
14. The API CI updates `apps/prod/rbx-catalog/kustomization.yml` in
    `rbx-infra`.
15. Argo CD syncs `rbx-catalog`.
16. `rbx-catalog-console` shows the new runtime entity through the API.

For `--dry-run`, Eden must not write, commit, push, or apply cluster state.

---

## 3. Eden Type Mapping

Eden product types are not identical to runtime catalog entity types.
The mapping is explicit:

| Eden type | Runtime entity_type | Registry path | Default runtime shape |
|---|---|---|---|
| `agent` | `agent` | `catalog/agents/<name>.yaml` | async API-invoked composable agent |
| `api` | `service` | `catalog/services/<name>.yaml` | sync API service |
| `web-static` | `product` | `catalog/products/<name>.yaml` | standalone browser-facing product |
| `fullstack` | `product` | `catalog/products/<name>.yaml` | standalone hybrid product |
| `cli` | `product` | `catalog/products/<name>.yaml` | standalone CLI product |

Status mapping:

| Eden phase | Runtime status |
|---|---|
| `seed` | `experimental` |
| `structuring` | `experimental` |
| `expansion` | `active` |
| `institutionalized` | `active` |

Domain mapping:

- If `--catalog-domain` is passed, Eden uses it after normalizing to snake case.
- For agents without `--catalog-domain`, Eden uses the owning product from
  `--product`.
- For non-agents without `--catalog-domain`, Eden uses the new product name.

`--catalog-domain` should be used when the product belongs to an existing
business or technical domain such as `trading_intelligence`, `platform`,
`runtime`, or `publishing`.

---

## 4. Human Review After Eden

Eden-generated runtime entities are valid starting points. They are not final
taxonomy decisions.

After `eden new`, a human should review the registry YAML and refine:

- `domain`
- `interaction_mode`
- `delivery_mode`
- `execution_role`
- `composition_mode`
- `invocation_surface`
- `loop_dependency`
- `inputs`
- `outputs`
- `side_effects`
- `responsibilities`
- `owner`
- `status`

Minimum review rule:

- Do not mark a runtime entity `active` until its interface and ownership are
  understood.
- Do not add new taxonomy fields without updating
  `rbx-catalog-registry/schemas/entity.schema.yaml`,
  `rbx-catalog-registry/docs/taxonomy.md`,
  `rbx-catalog-api/app/models/entity.py`, API tests, and console types if
  surfaced in UI.

---

## 5. Manual Registry Changes

Manual changes are still valid when adding non-product entities or refining
Eden-generated records.

Use manual edits for:

- loops
- tools
- shared services not provisioned by Eden
- post-creation taxonomy refinement
- inputs, outputs, side effects, and responsibilities

Manual flow:

1. Edit or add YAML under `rbx-catalog-registry/catalog/<type>/`.
2. Validate the YAML and taxonomy locally.
3. Run the API against the local registry.
4. Confirm `/catalog/entities` returns the entity.
5. Push `rbx-catalog-registry`.
6. Confirm the registry workflow dispatches the API rebuild.

Do not add mutation endpoints to `rbx-catalog-api`. Git remains the write path.

---

## 6. Local Development Setup

Expected local checkouts:

```text
~/apps/eden
~/apps/rbx-infra
~/apps/rbx-catalog-registry
~/apps/rbx-catalog-api
~/apps/rbx-catalog-console
```

Optional Eden config:

```yaml
# ~/.eden.yml
infra_path: ~/apps/rbx-infra
catalog_registry_path: ~/apps/rbx-catalog-registry
github_org: rbxrobotica
default_registry: ghcr.io/rbxrobotica
kubeconfig: ~/.kube/config-rbx
```

Build Eden:

```bash
cd ~/apps/eden
bun build src/index.ts --compile --outfile /tmp/eden-test
```

Run the API locally:

```bash
cd ~/apps/rbx-catalog-api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
RBX_REGISTRY_PATH=~/apps/rbx-catalog-registry uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Smoke the API:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/catalog/entities
curl http://127.0.0.1:8000/catalog/stats
```

Run the console locally:

```bash
cd ~/apps/rbx-catalog-console
npm install
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
```

Expected console URL:

```text
http://localhost:3000
```

---

## 7. Validation Before PR Or Push

For Eden changes:

```bash
cd ~/apps/eden
bun build src/index.ts --compile --outfile /tmp/eden-test
```

For registry YAML changes:

```bash
cd ~/apps/rbx-catalog-registry
python3 - <<'PY'
from pathlib import Path
import sys
import yaml

root = Path("catalog")
required = {"name", "entity_type", "domain", "status", "version"}
enums = {
    "entity_type": {"agent", "product", "loop", "tool", "service"},
    "status": {"active", "deprecated", "experimental"},
    "interaction_mode": {"sync", "async", "stream", "hybrid"},
    "delivery_mode": {"single", "stream", "evented", "batch"},
    "execution_role": {"background", "interactive", "orchestrator", "sidecar"},
    "composition_mode": {"composable", "embedded", "infrastructure", "standalone"},
    "invocation_surface": {"api", "chat", "cli", "event"},
    "loop_dependency": {"none", "optional", "required"},
}

errors = []
for path in sorted(root.rglob("*.yaml")):
    data = yaml.safe_load(path.read_text()) or {}
    missing = required - set(data)
    if missing:
        errors.append(f"{path}: missing {sorted(missing)}")
    for field, allowed in enums.items():
        value = data.get(field)
        if value is not None and value not in allowed:
            errors.append(f"{path}: invalid {field}={value}")

if errors:
    print("\n".join(errors))
    sys.exit(1)
print("catalog-yaml-ok")
PY
```

For API changes:

```bash
cd ~/apps/rbx-catalog-api
source .venv/bin/activate
pytest
```

For console changes:

```bash
cd ~/apps/rbx-catalog-console
npm run build
```

For infra changes:

```bash
cd ~/apps/rbx-infra
git diff --check
```

---

## 8. CI/CD Contract

Registry to API:

1. Push to `rbx-catalog-registry` affecting `catalog/**` or `schemas/**`.
2. Registry workflow dispatches `registry-updated` to `rbx-catalog-api`.
3. API workflow checks out the exact registry commit into `registry/`.
4. API workflow runs tests.
5. API workflow builds `ghcr.io/rbxrobotica/rbx-catalog-api:sha-<7>`.
6. API workflow updates `apps/prod/rbx-catalog/kustomization.yml` in
   `rbx-infra`.
7. Argo CD syncs the API deployment.

Console to infra:

1. Push to `rbx-catalog-console`.
2. Console workflow builds with
   `NEXT_PUBLIC_API_URL=https://api.catalog.rbx.ia.br`.
3. Console workflow pushes
   `ghcr.io/rbxrobotica/rbx-catalog-console:sha-<7>`.
4. Console workflow updates `apps/prod/rbx-catalog/kustomization.yml` in
   `rbx-infra`.
5. Argo CD syncs the console deployment.

Eden to infra and registry:

1. Eden writes `rbx-infra`.
2. Eden writes `rbx-catalog-registry`.
3. Eden pushes both.
4. Infra sync handles product deployment.
5. Registry dispatch handles catalog visibility.

Required GitHub secrets:

| Repository | Secret | Purpose |
|---|---|---|
| `rbx-catalog-registry` | `RBX_CATALOG_API_TOKEN` | Dispatch `registry-updated` to `rbx-catalog-api` |
| `rbx-catalog-api` | `INFRA_DEPLOY_KEY` | Push image tag updates to `rbx-infra` |
| `rbx-catalog-console` | `INFRA_DEPLOY_KEY` | Push image tag updates to `rbx-infra` |

---

## 9. Production Wiring

Production namespace:

```text
rbx-catalog
```

Production URLs:

```text
https://catalog.rbx.ia.br
https://api.catalog.rbx.ia.br
```

Production state in `rbx-infra`:

```text
apps/prod/rbx-catalog/
gitops/app-of-apps/rbx-catalog.yml
```

Kustomize image tags:

```text
apps/prod/rbx-catalog/kustomization.yml
```

The API image bakes a registry snapshot into `/opt/rbx-catalog-registry`.
The API does not watch the registry live; changing registry files requires a new
API image or API process restart.

The console bakes `NEXT_PUBLIC_API_URL` into the browser bundle at build time.
Changing it at container runtime does not update client-side fetches.

---

## 10. Debugging Runbooks

### Entity was created by Eden but does not appear in console

Check in this order:

1. Is the entity present under `rbx-catalog-registry/catalog/**`?
2. Did the registry commit reach the remote branch that triggers CI?
3. Did the registry workflow dispatch `registry-updated`?
4. Did `rbx-catalog-api` rebuild using the registry commit?
5. Did the API workflow update `rbx-infra/apps/prod/rbx-catalog/kustomization.yml`?
6. Did Argo CD sync `rbx-catalog`?
7. Does `https://api.catalog.rbx.ia.br/catalog/entities/<name>` return the entity?
8. Was the console image built with the expected `NEXT_PUBLIC_API_URL`?

### Product is deployed but missing from runtime catalog

Likely causes:

- Eden was run before runtime registry integration.
- `--dry-run` was used.
- Registry push failed after infra push.
- The entity YAML was created on a branch that does not trigger CI.

Fix:

1. Add or repair the YAML in `rbx-catalog-registry`.
2. Validate locally.
3. Push the registry branch that triggers CI.

### API starts but entity count is lower than expected

Likely causes:

- Invalid YAML was skipped.
- `RBX_REGISTRY_PATH` points at the wrong checkout.
- API image was built with an old registry snapshot.

Fix:

1. Validate all registry YAML.
2. Check API logs for validation warnings.
3. Confirm the API image tag corresponds to the expected registry commit.

---

## 11. Known Gaps

The current system still has these gaps:

- Registry YAML validation is not yet packaged as a first-class script.
- `rbx-catalog-api` logs validation errors and continues; it should eventually
  fail startup or fail CI on invalid entities.
- The console `/entities` route fetches from the browser, so the API must remain
  publicly reachable until the console proxies or server-renders that route.
- `NEXT_PUBLIC_API_URL` is build-time, which makes API URL changes require a
  console rebuild.
- The legacy `rbx-infra/catalog/products.yml` still exists and must remain in
  sync with Eden until the portfolio fields are modeled in the runtime registry.
- DNS for `catalog.rbx.ia.br` and `api.catalog.rbx.ia.br` should be fully
  codified in infra.
- No automated post-deploy smoke test exists for the catalog system yet.

---

## 12. References

Repo-local documentation:

- `eden/README.md`
- `eden/ARCHITECTURE.md`
- `rbx-catalog-registry/docs/IMPLEMENTATION-GUIDE.md`
- `rbx-catalog-api/docs/IMPLEMENTATION-GUIDE.md`
- `rbx-catalog-console/docs/IMPLEMENTATION-GUIDE.md`
- `rbx-infra/docs/RBX-CATALOG-IMPLEMENTATION-GUIDE.md`

These documents should link back to this guide and avoid redefining the
cross-repository lifecycle independently.
