# RBX Catalog Implementation Guide

**Last updated:** 2026-04-08
**Scope:** `rbx-catalog-registry`, `rbx-catalog-api`, `rbx-catalog-console`, and RBX GitOps integration in `rbx-infra`

**Per-repo implementation guides** (focused, repo-specific):
- `rbx-catalog-registry/docs/IMPLEMENTATION-GUIDE.md` — entity authoring, schema evolution, CI/CD trigger
- `rbx-catalog-api/docs/IMPLEMENTATION-GUIDE.md` — API architecture, endpoints, Docker build, CI/CD → rbx-infra
- `rbx-catalog-console/docs/IMPLEMENTATION-GUIDE.md` — console architecture, build-time env, CI/CD → rbx-infra

This document covers the full cross-system picture. Read it when you need to understand how the pieces fit together.

---

## 1. System Overview

The RBX catalog system is the internal runtime catalog foundation for describing, serving, and browsing RBX runtime entities.

It exists to establish one canonical source of truth for what runtime entities exist and how they are classified, then expose that information consistently to software and humans.

The architectural philosophy is strict and must be preserved:

- `rbx-catalog-registry` defines what exists
- `rbx-catalog-api` loads, validates, and serves what exists
- `rbx-catalog-console` makes what exists visible to humans

The registry is canonical. The API is read-only over the registry. The console is a browser, not an editor.

The current initial catalog entities are:

- `rbx-blog-publisher`
- `robson-conversation`
- `rbx-agent-loop`

The current production shape is:

- Namespace: `rbx-catalog`
- Console URL: `https://catalog.rbx.ia.br`
- API URL: `https://api.catalog.rbx.ia.br`
- Registry delivery model: baked into the API image at build time

## 2. Repository Structure

| Repository | Responsibility | What belongs here | What does not belong here |
|---|---|---|---|
| `rbx-catalog-registry` | Canonical catalog data and taxonomy | YAML entities, schema reference, taxonomy docs, naming docs | API runtime code, UI code, deployment manifests, business logic |
| `rbx-catalog-api` | Read-only catalog serving layer | FastAPI app, loader, Pydantic models, tests, API container build, API CI/CD | Source catalog YAML ownership, UI logic, cluster manifests |
| `rbx-catalog-console` | Human-facing browsing surface | Next.js app, API client, UI components, web container build, console CI/CD | Registry canonical data, API write logic, cluster manifests |
| `rbx-infra` | Production deployment source of truth | Kubernetes manifests, Argo CD apps, Kustomize overlays, infra docs, validation workflows | Application source code, app business logic, runtime taxonomy ownership |

Responsibilities by repo:

1. `rbx-catalog-registry`
   Contains `catalog/`, `schemas/`, and `docs/`.
   No runtime process exists here.

2. `rbx-catalog-api`
   Loads YAML from the registry path, validates into Pydantic models, stores the catalog in memory at startup, and serves read-only endpoints.

3. `rbx-catalog-console`
   Consumes the API through `NEXT_PUBLIC_API_URL` and renders home, entity list, and entity detail routes.

4. `rbx-infra`
   Owns the production namespace, deployments, services, ingresses, Argo CD application, and image tag state for production rollout.

## 3. Core Concepts

The catalog models runtime entities using a small shared vocabulary. Current taxonomy definitions live in `rbx-catalog-registry/docs/taxonomy.md`.

### `entity_type`

Primary classification of an entity.

Allowed values:

- `agent`
- `product`
- `loop`
- `tool`
- `service`

### `interaction_mode`

How the entity interacts with its caller over time.

Allowed values:

- `sync`
- `async`
- `stream`
- `hybrid`

### `execution_role`

The entity’s role in runtime execution.

Allowed values:

- `background`
- `interactive`
- `orchestrator`
- `sidecar`

### `composition_mode`

How the entity is assembled into runtime systems.

Allowed values:

- `composable`
- `embedded`
- `infrastructure`
- `standalone`

### How the catalog models runtime

Each YAML entity describes runtime identity, classification, and interface shape. The catalog does not model execution implementation. It models metadata required to reason about runtime composition.

Typical fields:

- `name`
- `entity_type`
- `domain`
- `status`
- `version`
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

## 4. Local Development Setup

### Registry

The registry has no runtime process.

```bash
cd ~/apps/rbx-catalog-registry
```

### API

```bash
cd ~/apps/rbx-catalog-api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Expected local API URL:

- `http://127.0.0.1:8000`

Useful verification:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/catalog
curl http://127.0.0.1:8000/catalog/entities
curl http://127.0.0.1:8000/catalog/stats
```

### Console

```bash
cd ~/apps/rbx-catalog-console
npm install
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
```

Expected local console URL:

- `http://localhost:3000`

Useful verification:

1. Open `http://localhost:3000/`
2. Open `http://localhost:3000/entities`
3. Open `http://localhost:3000/entities/rbx-blog-publisher`

### Local working state

The system is working locally when:

- `/health` returns `{"status":"healthy"}`
- `/catalog/entities` returns the expected entities
- the console home page shows stats and entities
- the entities list page shows the three entities
- the entity detail page renders `rbx-blog-publisher`

## 5. Data Flow

The runtime chain is:

1. Registry YAML files live in `rbx-catalog-registry/catalog/`
2. API startup resolves `RBX_REGISTRY_PATH`
3. API loader recursively reads `RBX_REGISTRY_PATH/catalog/**/*.yaml`
4. YAML is parsed and validated into `CatalogEntity` objects
5. The API stores the loaded catalog in memory for the process lifetime
6. The API serves read-only endpoints such as `/catalog/entities`
7. The console fetches the API using `NEXT_PUBLIC_API_URL`
8. The browser renders the catalog UI

Important runtime behavior:

- The API loads once at startup. It does not watch the registry live.
- The console does not read the registry directly.
- The `/entities` route is client-rendered and fetches in the browser.
- The home page and detail page use server-side fetches against the API.

## 6. Docker and Build

### API image

The API image is defined in `rbx-catalog-api/Dockerfile`.

Build behavior:

1. Start from `python:3.12-slim`
2. Install pinned Python dependencies from `requirements.txt`
3. Copy the FastAPI application code into `/app`
4. Copy a `registry/` directory into `/opt/rbx-catalog-registry`
5. Set `RBX_REGISTRY_PATH=/opt/rbx-catalog-registry`
6. Start `uvicorn` on port `8000`

Important build assumption:

- The Docker build context must contain a `registry/` directory populated with the contents of `rbx-catalog-registry`

This is why the API GitHub Action explicitly checks out the registry repo into `registry/` before building.

### Console image

The console image is defined in `rbx-catalog-console/Dockerfile`.

Build behavior:

1. Install dependencies with `npm ci`
2. Build the Next.js app with `output: "standalone"`
3. Bake `NEXT_PUBLIC_API_URL` at build time
4. Copy `.next/standalone` and `.next/static` into the final runtime image
5. Run the standalone server with `node server.js`

Important build assumptions:

- `NEXT_PUBLIC_API_URL` is a build-time value for the browser bundle
- the production image expects port `3000`
- the console image does not contain registry content and does not need it

## 7. CI/CD Flow

### API workflow

File: `rbx-catalog-api/.github/workflows/deploy.yml`

Triggers:

- push to `main`
- `repository_dispatch` event `registry-updated`
- manual dispatch

Flow:

1. Check out API repo
2. Resolve registry ref from event payload or default to `main`
3. Check out `rbx-catalog-registry` into `registry/`
4. Run API tests
5. Build and push `ghcr.io/rbxrobotica/rbx-catalog-api`
6. Compute immutable image tag `sha-<7chars>`
7. Clone `rbx-infra`
8. Update `apps/prod/rbx-catalog/kustomization.yml`
9. Commit and push the image tag change

### Console workflow

File: `rbx-catalog-console/.github/workflows/deploy.yml`

Triggers:

- push to `main`
- manual dispatch

Flow:

1. Check out console repo
2. Install dependencies
3. Build the app with `NEXT_PUBLIC_API_URL=https://api.catalog.rbx.ia.br`
4. Build and push `ghcr.io/rbxrobotica/rbx-catalog-console`
5. Clone `rbx-infra`
6. Update `apps/prod/rbx-catalog/kustomization.yml`
7. Commit and push the image tag change

### Registry-triggered rebuild

File: `rbx-catalog-registry/.github/workflows/deploy.yml`

Triggers:

- push to `main` affecting `catalog/**` or `schemas/**`
- manual dispatch

Flow:

1. Push to registry repo
2. Workflow dispatches `registry-updated` to `rbx-catalog-api`
3. API workflow rebuilds the API image with the new registry snapshot
4. API workflow updates `rbx-infra`
5. Argo CD syncs the updated API deployment

### How `rbx-infra` is updated

Both API and console workflows update `apps/prod/rbx-catalog/kustomization.yml` by changing `newTag:` for the relevant GHCR image.

This matches the current RBX image deployment pattern already used by other RBX applications.

### How deploy is triggered

Deploy is GitOps-driven:

1. Application CI updates the image tag in `rbx-infra`
2. That commit lands in `rbx-infra/main`
3. Argo CD sees the manifest change
4. Argo CD auto-syncs the `rbx-catalog` Application
5. Kubernetes rolls the new Deployment revision

## 8. Infrastructure Integration (rbx-infra)

### Namespace

Production namespace:

- `rbx-catalog`

Files:

- `apps/prod/rbx-catalog/namespace.yml`
- `core/namespaces/rbx-catalog.yml`

### Deployments

Files:

- `apps/prod/rbx-catalog/api-deploy.yml`
- `apps/prod/rbx-catalog/console-deploy.yml`

Both deployments:

- run as separate Kubernetes Deployments
- use `ClusterIP` Services
- use Traefik ingress
- use cert-manager TLS
- avoid analytics nodes through node affinity

### Services

Files:

- `apps/prod/rbx-catalog/api-svc.yml`
- `apps/prod/rbx-catalog/console-svc.yml`

Ports:

- API service exposes port `8080` to container port `8000`
- Console service exposes port `80` to container port `3000`

### Ingress

Files:

- `apps/prod/rbx-catalog/api-ingress.yml`
- `apps/prod/rbx-catalog/console-ingress.yml`
- `apps/prod/rbx-catalog/middleware-https.yml`

Ingress model:

- Traefik ingress class
- `letsencrypt-prod` ClusterIssuer
- HTTP to HTTPS redirect via Traefik middleware

### Argo CD app-of-apps

File:

- `gitops/app-of-apps/rbx-catalog.yml`

This registers `rbx-catalog` as an Argo CD child application under the RBX app-of-apps model.

### Kustomize structure

Directory:

- `apps/prod/rbx-catalog/`

Kustomize responsibilities:

- declare the resource set for the app
- provide shared labels
- declare image names and current `newTag` values

### How images are referenced

The Kustomization defines:

- `ghcr.io/rbxrobotica/rbx-catalog-api`
- `ghcr.io/rbxrobotica/rbx-catalog-console`

The deployment YAML uses those image names, and Kustomize rewrites them to the current immutable `sha-...` tag at render time.

## 9. Deployment Process (Step-by-step)

### A. API or console code change

1. Make code changes in the relevant repo
2. Push to `main`
3. GitHub Actions builds and pushes a new GHCR image
4. The workflow clones `rbx-infra`
5. The workflow updates `apps/prod/rbx-catalog/kustomization.yml`
6. The workflow commits and pushes that change to `rbx-infra`
7. Argo CD detects the infra commit
8. Argo CD syncs `rbx-catalog`
9. Kubernetes rolls the updated workload

### B. Registry content change

1. Update YAML or schema in `rbx-catalog-registry`
2. Push to `main`
3. Registry workflow dispatches `registry-updated` to `rbx-catalog-api`
4. API workflow checks out the exact registry commit into `registry/`
5. API image is rebuilt with that registry snapshot
6. API image tag is updated in `rbx-infra`
7. Argo CD syncs the updated API deployment

### C. Infrastructure-only change

1. Modify `rbx-infra`
2. Commit and push to `main`
3. Argo CD auto-syncs the updated manifests

## 10. Environment and Secrets

### Required environment variables

API:

- `RBX_REGISTRY_PATH`

Local default:

- `~/apps/rbx-catalog-registry`

Container default:

- `/opt/rbx-catalog-registry`

Console:

- `NEXT_PUBLIC_API_URL`

Local default:

- `http://localhost:8000`

Production value:

- `https://api.catalog.rbx.ia.br`

Notes:

- the console code already defaults to `http://localhost:8000` if `NEXT_PUBLIC_API_URL` is unset
- exporting `NEXT_PUBLIC_API_URL` explicitly during local development is preferred because it makes the dependency visible and matches the production configuration model more closely

Container runtime values also set:

- `NODE_ENV=production`
- `HOSTNAME=0.0.0.0`
- `PORT=3000`

### Required GitHub secrets

`rbx-catalog-api`:

- `INFRA_DEPLOY_KEY`

`rbx-catalog-console`:

- `INFRA_DEPLOY_KEY`

`rbx-catalog-registry`:

- `RBX_CATALOG_API_TOKEN`

### GHCR visibility assumption

Current design assumes the GHCR packages are public, matching the current RBX standard for public deployment targets.

If the packages are private:

- the cluster will need an `imagePullSecret`
- both `rbx-catalog` Deployments must reference that secret
- the namespace must contain credentials for GHCR pull access

### DNS assumptions

The Kubernetes ingress definitions exist in `rbx-infra`.

The DNS assumption is:

- `catalog.rbx.ia.br` resolves to the cluster ingress IP
- `api.catalog.rbx.ia.br` resolves to the cluster ingress IP

Current weakness:

- `rbx.ia.br` DNS is not yet fully managed in Terraform inside `rbx-infra`

## 11. Production URLs and Exposure Model

### Console public URL

- `https://catalog.rbx.ia.br`

### API public URL

- `https://api.catalog.rbx.ia.br`

### Reasoning for exposure

The console currently depends on a public API because the `/entities` page is client-rendered and fetches directly from `NEXT_PUBLIC_API_URL` in the browser.

As implemented today, the cleanest consistent production model is:

- public console
- public read-only API

### Exposure risks

Current risks:

- no authentication
- no rate limiting
- public catalog metadata disclosure
- console availability partly depends on public API availability

This is acceptable only because the API is read-only and the catalog is not yet treated as sensitive operational data.

## 12. Known Gaps and Weak Points

1. DNS for `rbx.ia.br` catalog hosts is not fully managed in Terraform
2. Registry schema is descriptive, not machine-enforced
3. API loader currently logs invalid entity load errors and continues instead of failing startup
4. Duplicate entity names and filename/name drift are not fail-fast enforced
5. API is public because the console still has a client-side dependency on the public endpoint
6. No auth or policy layer exists on the API
7. No automated post-deploy smoke test exists yet for the catalog system
8. Registry changes require API image rebuild because the registry is baked into the image rather than mounted

## 13. Extension Guide

### Add a new agent to the catalog

1. Create a new YAML file in `rbx-catalog-registry/catalog/agents/`
2. Follow naming rules from `docs/naming-conventions.md`
3. Use taxonomy values from `docs/taxonomy.md`
4. Validate locally by running the API and fetching `/catalog/entities`
5. Push to `rbx-catalog-registry/main`
6. The registry workflow will trigger the API rebuild and redeploy

### Evolve schema safely

1. Update `rbx-catalog-registry/schemas/entity.schema.yaml`
2. Update `rbx-catalog-registry/docs/taxonomy.md` if taxonomy changes
3. Update `rbx-catalog-api/app/models/entity.py`
4. Update API tests
5. Update console `CatalogEntity` typing if fields are surfaced in UI
6. Only then add entities using the new fields

Do not change taxonomy only in one repo. The registry documentation and API validation must stay aligned.

### Extend API safely

1. Preserve read-only semantics
2. Update models first
3. Update loader or service layer if new filter behavior is needed
4. Add or adjust route handlers
5. Extend tests before deploy

Do not add registry mutation endpoints to `rbx-catalog-api`.

### Extend UI safely

1. Update `lib/api.ts` types and fetch wrappers
2. Extend route rendering or components
3. Keep the console read-only
4. Preserve the separation that the console consumes the API, not the registry repo directly

If a UI change adds client-side API usage, ensure the exposure model still makes sense.

## 14. Next Steps (Strictly prioritized)

1. Move `rbx.ia.br` DNS management into Terraform or codify the wildcard assumption explicitly
2. Add machine-enforced registry validation in CI for YAML structure, enums, and required fields
3. Make API startup fail fast on invalid entities, duplicates, and filename/name mismatches
4. Remove the console’s public API dependency by server-rendering or proxying the entities list route, then tighten API exposure
5. Add deployment smoke tests that verify console and API URLs after each image rollout

## Quick Resume Checklist

1. Open these repos:
   - `~/apps/rbx-catalog-registry`
   - `~/apps/rbx-catalog-api`
   - `~/apps/rbx-catalog-console`
   - `~/apps/rbx-infra`
2. Read:
   - this file
   - `rbx-catalog-registry/docs/taxonomy.md`
   - `rbx-infra/docs/CONTAINER-REGISTRY.md`
3. Run locally:
   ```bash
   cd ~/apps/rbx-catalog-api
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
   ```
   ```bash
   cd ~/apps/rbx-catalog-console
   npm install
   NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
   ```
4. Verify:
   - `http://127.0.0.1:8000/catalog/entities`
   - `http://localhost:3000/`
   - `http://localhost:3000/entities`
   - `http://localhost:3000/entities/rbx-blog-publisher`
5. Understand what is running:
   - registry is files only
   - API loads registry into memory
   - console reads the API
   - production deploy is controlled by `rbx-infra`
6. Check production wiring:
   - `rbx-infra/apps/prod/rbx-catalog/`
   - `rbx-infra/gitops/app-of-apps/rbx-catalog.yml`
   - `rbx-infra/gitops/projects/rbx-applications.yaml`
7. If extending next, start with:
   - catalog validation
   - API fail-fast loading
   - DNS codification for `catalog.rbx.ia.br` and `api.catalog.rbx.ia.br`
