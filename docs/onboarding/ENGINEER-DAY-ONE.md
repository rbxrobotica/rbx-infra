# Engineer Day One

**Audience:** new RBX engineer joining the infrastructure side.
**Goal:** by the end of day one, you can read the cluster, find a
broken service, and ship a small change through GitOps.

This document covers infrastructure access. For codebase setup
(Robson v3 / Rust / SvelteKit) see
`robson/docs/onboarding/DEVELOPER-QUICKSTART.md`.

---

## Mental model

RBX runs three planes on operator-managed VPS instances. Nothing
critical lives on a developer laptop.

```
Compute plane                     DNS plane              Data plane
─────────────                     ─────────              ───────────
k3s cluster                       PowerDNS               PostgreSQL (external)
  tiger    158.220.116.31          pantera ns1            jaguar 161.97.147.76
  jaguar   161.97.147.76           eagle  ns2             (also k3s agent +
  altaica  173.212.246.8                                   ParadeDB host)
  sumatrae 5.189.178.212
                                                        Postgres NEVER lives in
ArgoCD GitOps                     Records via tofu      the cluster, except for
cert-manager + LE TLS             dns-tofu-env.sh       per-test ephemeral DBs.
Traefik ingress
```

Read once before doing anything else:

- `CLAUDE.md` (root) — non-negotiable rules.
- `docs/infra/ARCHITECTURE.md` — node map and three-plane model.
- `docs/infra/DNS.md` — DNS day-to-day operations.
- `docs/infra/SECRETS.md` — `pass` store layout.
- `docs/ARGOCD-BEST-PRACTICES.md` — GitOps rules; especially the
  ServerSideApply ban.

---

## Access matrix

What you need before you can do anything useful. Ask the operator
or a senior engineer to set each item up. Mark `[x]` as you go.

- [ ] **GitHub access** — invitation to org `rbxrobotica` and
      collaborator on `ldamasio/robson`.
- [ ] **GHCR read** — pull access to
      `ghcr.io/rbxrobotica/*` and `ghcr.io/ldamasio/*`. With a
      personal `gh auth login`, you usually get this for free.
- [ ] **Kubeconfig** — a copy of `~/.kube/config-rbx` from the
      operator, OR your own service-account kubeconfig issued
      from the cluster. Read-only is fine for day one.
- [ ] **GPG key trusted by `pass`** — the `pass` store is shared
      via git + GPG-encrypted entries. Your public GPG key needs
      to be added as a recipient. Ask the operator.
- [ ] **SSH key on pantera and eagle** — only if you need to
      operate DNS directly. Most engineers do not.
- [ ] **Ansible vault password** — only for engineers who run
      bootstrap playbooks. Most engineers do not.
- [ ] **ArgoCD UI login** — see "ArgoCD UI" below.

You can read most of the system without any of the credentials
above. Production-affecting changes require the relevant access.

---

## Local toolchain

Install once:

```bash
# Required
curl -fsSL https://get.docker.com | sh    # docker (optional unless building images)
brew install kubectl                        # or your distro equivalent
brew install kustomize
brew install opentofu                       # tofu, NOT terraform
brew install pass gnupg                     # secrets retrieval
gh auth login                               # github cli

# Robson v3 dev (only if you'll edit application code)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
brew install node@20 pnpm
```

Optional:

- `k9s` — terminal UI for the cluster. Faster than raw kubectl
  for browsing.
- `jq`, `yq` — for parsing kubectl output and YAML configs.
- `dig` — DNS troubleshooting.

---

## First read: cluster health

```bash
export KUBECONFIG=~/.kube/config-rbx
kubectl get nodes
kubectl get ns
kubectl get pods -A | head -40
kubectl get app -n argocd                  # ArgoCD Applications
```

Expected baseline: 4 nodes Ready, ArgoCD applications mostly
`Synced + Healthy`. Some apps may show `Degraded` or
`Progressing` — that's normal, not an emergency. Compare with the
operator before assuming an incident.

---

## ArgoCD UI

URL: <https://argocd.rbx.ia.br>

User: `admin`

Password retrieval (operator-managed; rotated after first login):

```bash
KUBECONFIG=~/.kube/config-rbx \
  kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d; echo
```

If the secret has been deleted (best practice after first
rotation), ask the operator for the rotated password — it lives
in `pass` under the appropriate entry.

The UI is the canonical surface for **observing** cluster state.
Do not use it to mutate resources directly — all mutations go
through git via ArgoCD GitOps.

---

## DNS access (when you need it)

Most engineers won't touch DNS directly. When you need to add a
record, the path is:

1. Edit a `*.tf` file under `infra/terraform/dns/`.
2. Open a PR; reviewer approves.
3. Once merged, run `tofu plan` then `tofu apply` via the
   wrapper:
   ```bash
   ssh -f -N -L 18081:127.0.0.1:8081 root@149.102.139.33
   cd ~/apps/rbx-infra/infra/terraform/dns
   ../../scripts/dns-tofu-env.sh tofu plan
   ../../scripts/dns-tofu-env.sh tofu apply
   ```

The wrapper injects `PDNS_API_KEY` from your `pass` store and
points the provider at the SSH tunnel.

If something goes wrong, see
`docs/runbooks/DNS-TROUBLESHOOTING.md`. The 2026-04-25
PDNS-CRASHLOOP incident in `docs/incidents/` is required reading
before you operate DNS the first time — the failure mode is
non-obvious.

---

## Your first PR — adding a small thing

A typical "add a new app" workflow, simplified:

1. **Create namespace** (if needed) in
   `core/namespaces/<name>.yml`.
2. **Add manifests** under `apps/prod/<your-app>/`:
   `deployment.yml`, `svc.yml`, `ingress.yml`, `kustomization.yml`.
3. **Register the ArgoCD Application** in
   `gitops/app-of-apps/<your-app>.yml` (template in CLAUDE.md).
4. **Open a PR**, get review, merge.
5. ArgoCD detects the new Application and syncs it within a
   minute or two. Watch in the UI.

Full procedure: `docs/runbooks/ADD-NEW-APPLICATION.md` (when
written; for now, follow the CLAUDE.md template).

---

## Where to go when things break

| Symptom | Document |
|---------|----------|
| ArgoCD Application stuck `OutOfSync` | `docs/INCIDENT-2026-03-28-ARGOCD-OUTOFSYNC.md` (case study) |
| DNS not resolving / `pdns` crashlooping | `docs/runbooks/DNS-TROUBLESHOOTING.md` + `docs/incidents/INCIDENT-2026-04-25-PDNS-CRASHLOOP.md` |
| TLS cert stuck `Ready: False` | `docs/runbooks/CERT-MANAGER-DEBUG.md` |
| Need to rotate a secret | `docs/infra/SECRETS.md` |
| Need to add a database for a new service | Ansible bootstrap playbooks (operator only); per-app schema migrations live in the app repo |

---

## Non-negotiable rules to internalize

From `CLAUDE.md`:

1. No secrets in git — ever.
2. No manual `kubectl apply` — all changes through GitOps.
3. English only.
4. K9s preferred for cluster ops, not raw kubectl chains.
5. GHCR only for image hosting (no Docker Hub).
6. **No `ServerSideApply=true`** in ArgoCD Applications. See
   `docs/ARGOCD-BEST-PRACTICES.md`.
7. `ROBSON_BINANCE_USE_TESTNET` is forbidden in `apps/prod/`. Its
   presence there is an incident.

Add an eighth from operator practice (2026-04-25):

8. **Postgres never lives in the cluster** except for ephemeral
   dev/test databases. PowerDNS, ParadeDB, and any application
   datastore use external Postgres on dedicated VPS hosts.

---

## Ask early, ask in writing

If you're stuck on access or a procedure, ask in the team channel
or open a draft PR with your question in the description. The
infrastructure has accumulated subtle constraints — the docs
exist to capture them, but reality moves faster than the docs.
When in doubt, the operator and the senior engineers are
authoritative; CLAUDE.md is the second-best source.
