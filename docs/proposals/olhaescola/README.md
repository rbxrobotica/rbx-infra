# Olha Escola — preparação de lançamento

Proposta sem ativação. Stack: SvelteKit/adapter-node, Go, PostgreSQL/PostGIS externo e k3s RBX. Marca pública: Olha Escola; assinatura: Cidadania na Escola; domínio escolhido: olhaescola.com.br. Responsável informado pelo operador: Leandro Damasio; contato público: ceo@rbxsystems.ch.

## O que este diff faz

Adiciona 12 recursos em `apps/prod/olhaescola`, no padrão de imagens GHCR/Pattern R, e inclui o render desse overlay no CI. Os dois Deployments têm **zero réplicas**, envios/moderação/revisão jurídica estão **fechados**, e não há Application em `gitops/app-of-apps`. Nenhum Secret ou permissão RBAC é criado. `application.candidate.yml` e o fragmento do AppProject ficam somente nesta pasta de documentos, fora da árvore registrada do ArgoCD.

O Service PostgreSQL é selectorless com Endpoints apontando ao host externo existente jaguar, conforme `docs/infra/DATABASE.md`. Nenhum banco roda no k3s. O label transformer não pode injetar um selector nesse Service.

As imagens referenciam o commit local de runtime **17dfa5e22cd98c952986757218f15bda822b2d14**, construído e testado. Os caminhos `ghcr.io/rbxrobotica/olhaescola-api` e `ghcr.io/rbxrobotica/olhaescola-web` são destinos propostos: **as imagens ainda não foram publicadas no registry**. A tag presente não comprova disponibilidade no GHCR e não autoriza ativação. O repositório remoto do aplicativo ainda não foi resolvido; a consulta de `rbxrobotica/olhaescola` não encontrou um repositório acessível.

## Verificação de 13/10/2026

- `kubectl kustomize apps/prod/olhaescola`: 12 recursos renderizados.
- `scripts/check-image-convention.sh apps/prod/olhaescola/*.yml`: sem violações Pattern R.
- `scripts/validate-olhaescola-launch-preparation.py`: zero réplicas, flags fechadas, imagens fixadas, frontend sem Secrets, API sem Ingress, banco selectorless e sem registro de Application.
- Kubeconform v0.6.4, checksum igual ao workflow existente: 11 recursos válidos, 0 inválidos, 0 erros, 1 ignorado (Middleware CRD sem schema). Isso não valida a CRD no cluster.
- Imagens locais executadas com filesystem somente leitura e privilégios removidos. Banco descartável com CA de teste, `sslmode=verify-full`, nome do certificado verificado e role `olhaescola_app` separada da migração. Importação real de 138.086 escolas / 116.979 geocodificadas; busca, privacidade e OG HTTP 200. Envios fechados retornam 503; saúde operacional sem autenticação retorna 401.
- Testes Go: exclusão persistente após falha de armazenamento, expiração de fotos privadas sem apagar publicadas/recentes, análise tardia preservando recusa humana, credenciais S3 separadas da AWS e regiões independentes. Comprehend usa `eu-central-1`; Rekognition usa `sa-east-1`. [Regiões Comprehend](https://docs.aws.amazon.com/general/latest/gr/comprehend.html), [regiões Rekognition](https://docs.aws.amazon.com/general/latest/gr/rekognition.html).
- WHOIS Registro.br em 13/10/2026 18:44 -03:00: `% No match for olhaescola.com.br`. Nenhum domínio foi registrado.

A conta Contabo/endpoint `https://eu2.contabostorage.com`, região `eu2`, e a origem das chaves `rbx/s3/*` aparecem no repositório RBX. Isso não comprova existência de um bucket próprio privado nem autorização dessas chaves para ele. A API agora exige `S3_ACCESS_KEY_ID`/`S3_SECRET_ACCESS_KEY` próprios para S3 compatível; as chaves AWS da detecção não são reutilizadas implicitamente.

A ServiceAccount Codex não pode listar nodes nem ler o Deployment Traefik em kube-system; as duas consultas retornaram Forbidden. SSH ao alias jaguar não resolveu nesta máquina, e ao IP documentado falhou na verificação da chave de host. Não foram alterados kubeconfig, RBAC ou known_hosts. PostGIS, DB/roles, TLS do PostgreSQL, labels reais do Traefik, cadeia XFF e CNI/NetworkPolicy **não foram confirmados ao vivo**.

## Sequência de ativação a revisar e autorizar

1. Registrar o domínio pelo operador; confirmar conta de detecção AWS, Turnstile e orçamento. As credenciais ficam em `pass`, sem copiar valores para PR/chat.
2. Definir repositório remoto privado do app e publicar imagens aprovadas; verificar os digests no GHCR antes de trocar réplicas. Não disparar workflow de publicação nem push de imagem sem autorização específica.
3. Provisionar DB `olhaescola`, role proprietária `olhaescola_migrator` e role limitada `olhaescola_app` em jaguar; instalar/verificar PostGIS, pg_trgm e unaccent com administrador. Bootstrap e pg_hba devem usar a lista real de nodes e podCIDRs. Executar migração/importação com a role migradora, depois os grants limitados do app. Ensaio local de grants passou, mas não comprova o host de produção.
4. Confirmar certificado/CA do PostgreSQL. `DATABASE_URL` usa `olhaescola-postgres.olhaescola.svc` com `sslmode=verify-full`; `PG_TLS_SERVER_NAME` aponta ao DNS **verificado** do certificado externo, preservando a validação da cadeia. A CA privada, se necessária, deve ser montada. Não trocar para `sslmode=disable` ou `require` para contornar falha.
5. Provisionar bucket dedicado privado, sem versionamento/retention lock que impeça apagar a foto, e credenciais limitadas. Não usar `rbx-content` ou bucket público. Verificar acesso, permissão de exclusão, backup e política de retenção antes de publicar uploads.
6. Criar `olhaescola-secrets` e `ghcr-pull-secret` pelo fluxo RBX. Chaves previstas: `DATABASE_URL`, `DEVICE_SIGNING_SECRET`, `ADMIN_TOKEN`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY`, e `PG_TLS_SERVER_NAME` quando necessário. Session token só se usado. Não copiar a chave compartilhada do armazenamento sem avaliar seu escopo.
7. Confirmar/revisar a retenção privada proposta de 7 dias (configurável entre 1 e 30), política jurídica e quem revisará a fila. Fotos recusadas/removidas saem do mural antes da tentativa de exclusão. A fila durável repete falhas; conteúdo aprovado permanece até remoção. Reconciliação de objetos órfãos de uploads sem commit e versões/backups do bucket ainda requer rotina operacional; não prometer eliminação física total sem esse ensaio.
8. Em uma mudança de ativação separada, adicionar o namespace ao AppProject, a Namespace a core/namespaces conforme runbook, registrar a Application candidata com sync manual e ajustar réplicas. Manter envios fechados no primeiro sync. Validar certificado público, proxy/IP, probes e backup/restore; testar WhatsApp real em Android e iPhone.
9. Só depois de ensaiar detectores com imagens consentidas, confirmar revisores e resolver as pendências, alterar `MODERATION_ENABLED`, `LEGAL_REVIEW_APPROVED` e `PUBLIC_SUBMISSIONS_ENABLED`. A abertura pública é uma operação distinta do primeiro deploy fechado.

## Operação inicial

Saúde pública `/api/health` verifica banco. Saúde privada `/api/admin/health`, com a chave da moderação em Authorization, mostra análise pendente, revisão humana, análise indisponível, idade da fila e exclusões pendentes. Não pôr chave em URL. Acompanhar esses dados junto com erros do serviço; fila parada/exclusões persistentes exigem intervenção. Não há alarme automático integrado neste diff.

Rollback de recebimento: voltar `PUBLIC_SUBMISSIONS_ENABLED` para false e confirmar que POST falha fechado, mantendo o mural. Mudanças em produção precisam da autorização exata indicada em `AGENTS.md`; este PR não aplica nem sincroniza recursos.
