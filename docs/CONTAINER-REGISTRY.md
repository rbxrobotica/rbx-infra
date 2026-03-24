# Container Registry Standard

## Registry oficial: GitHub Container Registry (GHCR)

Todos os produtos RBX Systems usam **`ghcr.io/rbxrobotica/<produto>`** como registry padrão.

### Por que GHCR e não Docker Hub?

- **Zero secrets extras**: usa `GITHUB_TOKEN` (automático em toda GitHub Action).
- **Integrado à org**: packages aparecem em `github.com/orgs/rbxrobotica/packages`.
- **Sem rate limit** problemático para pulls do cluster.
- **Auditoria nativa**: cada imagem rastreável ao commit que a gerou.

---

## Naming convention

```
ghcr.io/rbxrobotica/<produto>:<tag>
```

| Produto | Imagem |
|---------|--------|
| rbx-ia-br (frontend) | `ghcr.io/rbxrobotica/rbx-ia-br` |
| truthmetal | `ghcr.io/rbxrobotica/truthmetal` |
| robson | `ghcr.io/rbxrobotica/robson` |
| strategos | `ghcr.io/rbxrobotica/strategos` |
| thalamus | `ghcr.io/rbxrobotica/thalamus` |
| argos-radar | `ghcr.io/rbxrobotica/argos-radar` |

### Tags

| Tag | Uso |
|-----|-----|
| `sha-<7chars>` | Tag imutável gerada pelo CI a cada push (`sha-a1b2c3d`) |
| `latest` | Sempre aponta para o último build de `main` |

A kustomization em `apps/prod/<produto>/kustomization.yml` usa `newTag: sha-<7chars>` atualizado pelo CI — nunca `latest` em produção (exceto durante bootstrap inicial).

---

## CI/CD template (GitHub Actions)

```yaml
env:
  IMAGE_NAME: ghcr.io/rbxrobotica/<produto>

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write          # necessário para push no GHCR

    steps:
      - uses: actions/checkout@v4

      - name: Set image tag
        id: tag
        run: echo "sha=sha-$(echo $GITHUB_SHA | cut -c1-7)" >> $GITHUB_OUTPUT

      - uses: docker/setup-buildx-action@v3

      - name: Login to GHCR
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          push: true
          tags: |
            ${{ env.IMAGE_NAME }}:${{ steps.tag.outputs.sha }}
            ${{ env.IMAGE_NAME }}:latest
          cache-from: type=gha
          cache-to: type=gha,mode=max

      - name: Update image tag in rbx-infra
        env:
          INFRA_DEPLOY_KEY: ${{ secrets.INFRA_DEPLOY_KEY }}
        run: |
          mkdir -p ~/.ssh
          echo "$INFRA_DEPLOY_KEY" > ~/.ssh/infra_deploy_key
          chmod 600 ~/.ssh/infra_deploy_key
          ssh-keyscan github.com >> ~/.ssh/known_hosts
          export GIT_SSH_COMMAND="ssh -i ~/.ssh/infra_deploy_key -o IdentitiesOnly=yes"

          git clone git@github.com:rbxrobotica/rbx-infra.git /tmp/rbx-infra
          cd /tmp/rbx-infra
          sed -i "s|newTag:.*|newTag: ${{ steps.tag.outputs.sha }}|" \
            apps/prod/<produto>/kustomization.yml
          git config user.email "ci@rbx.ia.br"
          git config user.name "RBX CI"
          git add apps/prod/<produto>/kustomization.yml
          git diff --cached --quiet || \
            git commit -m "chore(<produto>): deploy ${{ steps.tag.outputs.sha }} [skip ci]"
          git push
```

### Secrets necessárias por repo

| Secret | Origem | Propósito |
|--------|--------|-----------|
| `GITHUB_TOKEN` | **Automática** | Push de imagem no GHCR |
| `INFRA_DEPLOY_KEY` | Par SSH gerado manualmente | Push no rbxrobotica/rbx-infra |

Gerar `INFRA_DEPLOY_KEY`:
```bash
ssh-keygen -t ed25519 -C "ci@<produto>" -f ~/.ssh/<produto>_deploy -N ""
# Pública → rbxrobotica/rbx-infra → Settings → Deploy keys (Allow write access)
# Privada → rbxrobotica/<produto-repo> → Settings → Secrets → INFRA_DEPLOY_KEY
```

---

## Visibilidade das imagens

Packages no GHCR herdam a visibilidade do repo de origem por padrão. Para imagens de produtos públicos (rbx-ia-br, truthmetal, etc.), configurar como **público** em:

`https://github.com/orgs/rbxrobotica/packages` → Package settings → Change visibility

Imagens públicas no GHCR não requerem `imagePullSecret` no cluster.

---

## Migração de legado (Docker Hub → GHCR)

Para produtos que ainda usam `docker.io/ldamasio/<produto>`:

1. Atualizar CI workflow (ver template acima).
2. Atualizar `apps/prod/<produto>/kustomization.yml`: campo `name` no bloco `images:`.
3. Atualizar `apps/prod/<produto>/deploy.yml`: campo `image:` no container.
4. Fazer push → CI reconstrói e publica no GHCR → ArgoCD sincroniza.

Não é necessário migrar imagens antigas manualmente — o próximo CI push cria a imagem no GHCR do zero.
