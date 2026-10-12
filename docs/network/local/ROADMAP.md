# Roadmap da rede local e comunicação operacional

Atualizado em **2026-10-11**. As prioridades abaixo orientam entregas; não são
datas prometidas nem evidência de implantação. Inventário e testes concluídos:
[arquitetura](README.md) e [evidência LAN](EVIDENCE-2026-10-11.md).

## Decisões e esclarecimentos desta revisão

- Comunicação direta workstation → ThinkCentre por SSH/SFTP já foi validada.
  Esse caminho permanece disponível durante a evolução da mesh.
- Cliente IRC dedicado atende operação humana; integração no Strategos será
  adicional. O sintoma do Halloy está registrado, com causa ainda desconhecida.
- Konversation **1.10.26043** foi desinstalado por pedido do operador após
  avaliação do tema, perfil, distribuição e armazenamento de credenciais. A
  [revisão do piloto](KONVERSATION-PILOT.md) registra seu encerramento. Halloy
  permanece com tema e cofre atuais, em [diagnóstico](HALLOY-DIAGNOSTIC-2026-10-11.md).
  A preferência é distribuição leve e verificável, senha no cofre e consolidação
  posterior no bootstrap Mint.
- O operador concordou em revisar a senha própria do cliente nativo: o piloto
  admite conta/credencial revogável por aparelho sob a
  [política revisada](../../../infra/irc/docs/IDENTITY-CHATOPS.md).
- O padrão mobile de Strategos, Robson e demais produtos RBX não foi escolhido.
  O APK atual do Strategos não constitui uma decisão corporativa de framework.

## Sequência operacional

| Etapa | Entrega concreta | Evidência para concluir | Dependência |
| --- | --- | --- | --- |
| 0 — Base observada | Inventário, topologia e SSH/SFTP workstation → ThinkCentre | Testes e limites registrados; concluído | Nenhuma mudança no ZTE |
| 1 — IRC confiável no Mint | Diagnóstico Halloy de interface, conexão, memória e persistência, preservando tema e perfil atuais | Uso prolongado, atualizações visíveis, reconexão e histórico satisfatórios; hipótese confirmada antes de ajustar configuração | Túnel e conta humana existentes |
| 2 — Acesso privado definido | Política efetiva Headscale revisada, matriz de acesso e mudanças de host preparadas com reversão | Testes previstos de permitir/recusar acesso, IPv4/IPv6 e recuperação LAN | Inventário e identidade dos nós; inspeção da configuração real |
| 3 — Primeiro cliente móvel | Goguma no POCO com conta própria e endpoint TLS privado | Salas permitidas, revogação, histórico, Wi-Fi/dados móveis e tela bloqueada validados | Etapa 2; provisionamento do caminho e conta |
| 4 — Continuidade e segundo aparelho | Capacidade/backup da ThinkCentre avaliados e Samsung incluído se necessário | Restauração ensaiada e comportamento móvel medido no A02 | Etapa 3 para replicar o acesso móvel; revisão de capacidade pode ocorrer antes |
| 5 — Trabalho delegado | Uma tarefa delimitada workstation → ThinkCentre, com identificador e resultado verificável | Recebimento, execução autorizada, retorno de artefato/erro e ausência de duplicação | Identidades/escopo de execução e capacidade adequada |

### Etapa 1: investigar a falha do Halloy

O Halloy atual pode ficar aparentemente conectado e deixar de mostrar mensagens
até a janela ser reaberta, segundo o operador. Reabertura também refaz conexões
e consulta histórico; portanto, não identifica sozinha a causa.

Konversation foi removido; o tema Halloy foi aprovado. A observação inicial de
três minutos mostrou memória estável e os arquivos examinados passaram em
leitura JSON/gzip. Isso não descarta problemas após horas de uso ou falhas
lógicas de histórico. Registrar comportamento em uma jornada de trabalho,
incluindo período ocioso, suspensão e retomada.

Se houver nova falha, registrar horário, estado do cliente, CPU, memória,
sockets e estado do túnel antes de reabrir a janela, usando a
[captura somente leitura](HALLOY-DIAGNOSTIC-2026-10-11.md#captura-no-próximo-sintoma).
Logs de protocolo podem conter segredos e conversas: não habilitá-los como
primeira medida. Confirmar se houve mensagens novas por evidência autorizada;
ausência de movimento em um canal silencioso não demonstra travamento.

Só testar mudança de backend gráfico ou versão quando a evidência apontar
para essa hipótese. Alterar uma variável por vez e preservar configuração e
histórico para reversão. Não apagar caches nem trocar o tema como diagnóstico.

O [piloto humano e subagente Codex com contexto do Flight Deck](../../../infra/irc/docs/HUMAN-FLIGHTDECK-PILOT.md)
usa o canal e a mailbox existentes para testar pergunta, resposta e exibição
no Halloy. A sessão supervisionada não é um agente permanente nem valida a
execução delegada da etapa 5; serve como primeiro ensaio de conversa dirigida.
O aplicativo Flight Deck não foi chamado. A entrada humana delimitada está
implementada e desativada por padrão, com apresentação legível, sessão e claims
exclusivos, conforme o [runbook](../../../infra/irc/docs/HUMAN-QUERY-RUNBOOK.md).
A integração do executor, contexto mínimo e limites de consumo continuam no
[contrato de consulta via IRC](../../../infra/irc/docs/IRC-AGENT-QUERY-CONTRACT.md).
Os [ensaios isolados](../../../infra/irc/pilots/README.md) avançam essa trilha
com pre/post-call e auditoria do Thalamus, preparação do executor por assinatura
e avaliação de encaminhamento local. Continuam separados do atendimento IRC e
não concluem a implantação de um agente permanente.

### Etapas 2 e 3: preparar o acesso móvel

Inspecionar primeiro a política Headscale efetiva e os listeners/TLS do Ergo.
O template Git não comprova o estado em execução. Preparar uma mudança que
permita somente o serviço IRC necessário aos telefones e teste a negação de
serviços administrativos. Sem anunciar a LAN inteira nem configurar exit node.

A workstation está `NeedsLogin` no levantamento; a ThinkCentre já tem endereço
mesh. Antes de cadastrar novos nós, reconciliar a implementação real com a
[ADR-0011](../../adr/ADR-0011-headscale-sovereign-mesh-on-eagle.md), o
[runbook](../../runbooks/HEADSCALE-MESH.md) e decisões posteriores ainda não
integradas. Manter os caminhos existentes e `--accept-dns=false`. O piloto
móvel não depende de fechar acessos atuais.

**Pré-requisito de reconciliação identificado em 2026-10-11:** um relato técnico
fornecido pelo operador descreve política restritiva e uma implementação mais
recente mantida fora do `main`. A inspeção independente do `main` na revisão
`98ae09f1f1e4f128c2d9321de1d40e65fee707b1` confirmou que o template Headscale
não contém `policy`, e que o role substitui o arquivo efetivo antes de executar
`configtest` e reiniciar. A documentação oficial informa que ausência de
política carregada permite tráfego entre os nós. Portanto, há risco de
regressão de acesso ao reaplicar esse template; configuração, ambiente e
unidade reais ainda precisam ser reconciliados para comprovar o efeito exato.
Não usar esse playbook como caminho aprovado de expansão enquanto isso estiver
pendente. [Políticas Headscale](https://headscale.net/stable/ref/policy/).

Preservar commits, alterações pendentes e arquivos relevantes em destino
adequado antes de rebase ou integração. Este repositório é público; publicar
somente conteúdo revisado, sem segredos ou exportações brutas de configuração
operacional. Manter a cópia original e fazer a integração em worktree distinta.
O relato não substitui a verificação da branch remota, do backup ou da produção.

A reconciliação deve cobrir carregamento obrigatório e validação semântica da
política, testes de permitir/recusar, DERP e firewall, versão fixada do cliente
e restauração. O `main` ainda exige DERP embutido, libera STUN e permite
downgrade para o cliente fixado; esses pontos precisam concordar com a decisão
vigente. Não atribuir número definitivo à ADR local antes de reconciliar o
catálogo. Essa pendência não bloqueia o desenvolvimento IRC em ambiente isolado.

Separar papel administrativo e execução de agentes se a ThinkCentre concentrar
credenciais da frota. Identidades, tags e regras devem distinguir funções e
dispositivos; um único usuário administrativo não deve conceder o mesmo acesso
a todos. Endurecer SSH somente após verificar acesso por chave e recuperação.

Para o POCO, propor inicialmente `#rbx-notifications`, `#rbx-incidents` e
`#project-strategos`, ajustando à necessidade do operador antes de provisionar.
Essa lista é proposta, não uma ACL aplicada. Acesso à sala inclui o histórico
retido. A conta não precisa de OPER, fundação de canal ou shell remoto.

Testar revogação individual e encerramento das sessões, armazenamento local de
credenciais, retenção e notificações com tela bloqueada. Medir alertas após
reinício e troca Wi-Fi/dados móveis. Só depois ampliar para Samsung e avaliar
push, se os resultados justificarem a configuração adicional.

### Etapas 4 e 5: continuidade e delegação

A ThinkCentre apresentou disco em 86% e cerca de 16 GiB livres. Levantar consumo,
backup e disponibilidade antes de acrescentar serviços contínuos ou copiar
modelos/mídia. A workstation concentra as tarefas que exigem a GPU de 16 GB;
a ThinkCentre recebe tarefas CPU e serviços leves conforme capacidade.

O primeiro ensaio de delegação deve usar uma tarefa pequena, entradas/saídas
explícitas, limite de recursos, identificador, prazo e tratamento de erro.
A conta SSH humana existente não é automaticamente uma conta de execução de
agentes. Definir essa identidade antes de automação recorrente. IRC transporta
pedido/status/referência ao resultado; Git, artefatos e registros dos sistemas
preservam o trabalho durável. Mensagens não disparam shell arbitrário.

## Trilha paralela: padrão mobile dos produtos RBX

Esta decisão cobre Strategos, Robson e demais produtos. Não depende da adoção
de um cliente IRC e não deve bloquear a comunicação operacional.

**Os dois frameworks serão testados.** Ensaios de build e interface com dados
sintéticos podem começar já. A comparação funcional começa assim que houver
contratos mínimos de sessão, tenant, permissões e licença, com uma API de teste
comum. Não é necessário esperar a plataforma inteira; tampouco comparar apps
com fluxos de identidade diferentes e atribuir a diferença ao framework.

| Candidato | Situação oficial consultada em 2026-10-11 | Consequência para a avaliação |
| --- | --- | --- |
| Tauri v2 | Versão estável com Android/iOS e desktop | Candidato a referência inicial; validar plugins e requisitos reais |
| Wails v3 | Beta; API desktop estável, Android/iOS experimentais | Candidato para prova de conceito, com risco mobile explícito |

Fontes: [Tauri 2.0](https://v2.tauri.app/blog/tauri-20/),
[Wails v3 beta](https://v3.wails.io/blog/wails-v3-beta/) e
[guia mobile Wails](https://v3.wails.io/guides/mobile/).
Wails oferece implementação mobile real; não deve ser descartado como
exclusivamente desktop. Seu suporte experimental exige avaliação distinta.

Comparar uma mesma fatia de produto nos aparelhos existentes: login no navegador
do sistema e retorno ao app, retomada após suspensão, reconexão, notificações,
armazenamento seguro, atualização, legibilidade e consumo de memória/bateria.
O Samsung A02 deve participar, evitando uma decisão baseada só no aparelho mais
potente. Registrar também custo de manutenção Rust/Go e integrações Android.

Definir contratos de sessão, autorização por tenant/sala e entrega de eventos
independentemente do framework. Para Strategos, começar por salas/histórico
somente leitura; escrita e ações vêm com seus próprios contratos. Para Robson,
reutilizar identidade e padrões de interface quando fizer sentido, preservando
o domínio e as permissões do produto. Não impor acesso IRC a todo produto RBX.

O resultado dessa prova será uma decisão de arquitetura própria, com critérios,
limitações e atualização dos repositórios dos produtos. Nenhum framework foi
adotado, instalado ou migrado nesta revisão.

## Evolução coordenada do rbx-comms

O planejamento de camadas, catálogo de salas, identidade e produto está em
`rbx-comms/docs/architecture/sovereign-communications-roadmap.md`, acompanhado
de `irc-room-catalog.proposed.yaml`. Ambos são propostas, não configuração
aplicada. Preservam os sete canais atuais e consideram apenas três adições:
`#rbx-ops`, `#merovelis-ops` e `#rbx-merovelis-growth`, com membros definidos
explicitamente e sem herança automática entre empresas.

A primeira integração proposta é um alerta interno sanitizado para
`#rbx-notifications`, originado de monitoramento autenticado sobre saúde do
provedor de email ou ausência prolongada de confirmação. Ausência de recibo
não prova falha de entrega. O alerta não reenvia mensagens de clientes nem
executa recuperação automaticamente. Ensaiar em ambiente isolado antes do
rollout; falhas de host, energia e Internet podem afetar IRC e email juntos.

Os produtos reutilizam Identity para autenticação, autorização por recurso no
backend e Commerce para licenças/entitlements e seats. Licença não concede
acesso administrativo ou aprovação de execução. O modelo já existe nos
repositórios; migração e enforcement em produção ainda precisam ser confirmados.
IRC coordena funis internamente; contato com leads e Direct Response continuam
dependentes do público, consentimento e de experimentos mensuráveis nos canais
de atendimento. Nenhuma campanha foi iniciada por este planejamento.
