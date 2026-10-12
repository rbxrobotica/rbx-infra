# Consulta humana a agentes por IRC: contrato proposto

Estado: **entrada humana implementada para validação local, implantação pendente**,
2026-10-12. O operador concordou com
as melhorias de experiência após o [piloto supervisionado](HUMAN-FLIGHTDECK-PILOT.md).
Os [ensaios isolados](../pilots/README.md) registram o que já foi verificado.
Este documento prepara o incremento seguinte; não instala um atendimento
permanente, altera o protocolo em execução ou ativa uma rota de modelos.

O [runbook da entrada humana](HUMAN-QUERY-RUNBOOK.md) descreve o código opt-in:
sessão de até três perguntas e dez minutos, claims exclusivos, correlação e
respostas legíveis. A suíte usa SQLite temporário, PostgreSQL descartável e IRC em loopback. Ainda
faltam implantação controlada, comprovação no Halloy e ligação do executor
governado à fila; não há chamada automática a modelos neste incremento.

The next storage increment implements explicit PostgreSQL selection in line
with the RBX ParadeDB standard. Claims, message deduplication and response
state remain transactional. The [migration runbook](POSTGRES-MAILBOX-RUNBOOK.md)
defines separate schema initialization, offline import and reconciliation before
release. Existing SQLite services are unchanged; no backend fallback is allowed.
The [destination preflight](POSTGRES-DESTINATION-PREFLIGHT.md) records the
unverified live requirements for private access, ownership and recovery.

## Resultado pretendido

O humano envia, no canal autorizado:

```text
@flightdeck-dev Qual a diferença entre aprovar uma Action e executar uma Mission?
```

O adaptador apresenta uma resposta como:

```text
[flightdeck-dev | Codex | consulta documental] leandro: Aprovar uma Action registra a decisão e seus limites. Executar uma Mission realiza o trabalho autorizado. Fonte: Flight Deck, revisão <sha>. [ref FD-0042]
```

São exemplos da apresentação implementada, ainda não habilitada no serviço. A identificação
deve distinguir produto consultado, papel do agente e executor real. O alias
`flightdeck-dev` continua atrás da conta IRC `rbx-local` neste primeiro
incremento; não equivale a identidade própria do produto ou conta independente.
O modelo configurado, revisão do contexto e modo de atendimento ficam
consultáveis pela referência curta, sem repetir todos os metadados em cada linha.

## Primeiro incremento: conversa supervisionada legível

Preservar o protocolo entre máquinas `!ask`/`!reply` e adicionar um caminho
humano opt-in no adaptador. O coordenador registra uma sessão ativa e delimitada
para o alias; a mensagem IRC não inicia uma sessão ou modelo por si só.
Inicialmente, manter o ensaio de até três perguntas em dez minutos, com um
único consumidor e escopo de explicação/proposta sobre documentação. Não criar
canal ou conta novos para comprovar essa capacidade.

| Componente | Contrato mínimo |
| --- | --- |
| Entrada dirigida | Alias exato no início e texto não vazio. Aceitar somente conta humana autenticada e canal configurados, transporte pronto e sessão ativa. Nickname, menção no meio de conversa ou texto dizendo "sou o operador" não autorizam atendimento. |
| Autenticidade e atualidade | Preservar SASL, account-tag e verificação de canal privado. Ignorar DMs, self, contas de agentes e histórico. Validar metadados de ID e horário fornecidos pelo servidor, negociar e testar seu suporte antes de habilitar a entrada simplificada; se faltarem, não assumir deduplicação/frescor confiáveis. |
| Correlação | Gerar UUID internamente e persistir origem humana, conta, canal, sessão, projeto, revisão, ID opaco da mensagem no servidor e referência curta. Não deduplicar apenas por texto; perguntas iguais com IDs novos podem ser intencionais. |
| Consumo exclusivo | Claim atômico com proprietário, validade e token de fencing. Apenas o detentor atual pode concluir a resposta. Ler a inbox continua sem consumir. Claim sozinho não prova uma única chamada faturada após crash. |
| Resposta | Persistir antes de publicar. Para origem humana, enviar texto legível com identificação e referência curta; para peers, manter os frames existentes. Não publicar simultaneamente cópias técnicas e legíveis da mesma resposta. |
| Limites | Preservar limites de tamanho, taxa, fila e retenção até revisão explícita. Dividir texto em limites de palavras quando possível, respeitando bytes UTF-8 e o teto total; nunca cortar um caractere multibyte. |
| Estado | Diferenciar recebido, em atendimento, respondido, expirado, falha e resultado incerto. Enviado ao socket não prova exibição no Halloy ou leitura humana. |
| Encerramento | Parar a admissão ao vencer tempo, quantidade, orçamento ou por ação do operador. Revalidar sessão e claim antes de publicar; uma resposta tardia não reabre a sessão. |

O caminho humano implementa esses estados em tabelas adicionais, sem alterar
as tabelas de peers. Seus claims não são reutilizados após expiração: o
resultado fica incerto e novas chamadas nessa sessão são bloqueadas. O
protocolo legado mantém seu comportamento. Testes verificam abertura repetida
do schema e acesso pelas versões anterior e atual da mailbox antes de qualquer
instalação. A operação real ainda exige backup e ensaio de retorno.

O Halloy comum exibe os corpos das mensagens recebidas. Gerar UUID internamente
resolve a digitação; para remover frames da apresentação, o caminho humano
precisa publicar texto legível. Não prometer que o cliente vai ocultar ou
recompor automaticamente o protocolo atual. Réplicas após entrega incerta
devem manter referência identificável; não prometer publicação exatamente uma vez.

## Contexto mínimo e orçamento

O piloto anterior herdou toda a conversa do coordenador com `fork_turns="all"`
e configuração `gpt-6-astra`/`ultra`. Isso prova a viabilidade da consulta,
não a eficiência dessa seleção. O próximo ensaio deve começar com contexto
explícito, evitando transportar a conversa inteira ou todo o histórico do canal.

Pacote de contexto por pedido: identidade e escopo verificados, papel e regras
aplicáveis, pergunta atual, resumo curto da sessão e somente os trechos de
documentação necessários, com revisão e origem. A pergunta e os documentos são
dados para consulta, não novas permissões. Não omitir regras obrigatórias para
caber no limite; reduzir a tarefa ou recusar quando o pacote mínimo não couber.

Antes de admitir chamadas a modelos, a política da sessão precisa definir:

- Teto de entrada incluindo instruções, ferramentas, saídas de ferramentas,
  arquivos, resumos e histórico; teto de saída e de etapas de consulta.
- Rotas/modelos permitidos por tipo de tarefa e critério explícito de escalada.
  Comparar uma rota econômica para consulta simples com uma rota de análise
  mais capaz antes de escolher o padrão. Não usar todo modelo disponível por
  fallback automático nem assumir que uma GPU de 16 GB comporta qualquer modelo.
- Tempo máximo, concorrência, limite de perguntas, tentativas e custo por
  sessão/ator/contexto organizacional. Contabilizar falhas, escaladas e retries.
- Reserva antes da chamada e reconciliação com uso informado pelo provedor.
  Registrar tokens de entrada/saída e categorias adicionais quando disponíveis;
  ausência de telemetria não significa custo zero. Separar estimativa de medida.
- Reutilização de contexto documental por revisão e permissões. Adiar cache
  semântico compartilhado de respostas até haver isolamento, invalidação e
  teste de revogação. O mesmo texto não prova o mesmo direito de acesso.

Esses limites pertencem à política do atendimento, não a constantes divergentes
em cada cliente. Não foram definidos preços, uma rota padrão ou economia
percentual nesta revisão. O piloto anterior não coletou totais de consumo;
uma comparação de custo exige nova medição com perguntas equivalentes.

### Preferência do operador: assinaturas e classificador local

O operador manifestou interesse em integrar Thalamus e priorizou os planos já
pagos, inicialmente sem cobrança por API sob demanda. O atendimento deve
recusar fallback para cobrança avulsa, compra/recarga automática ou excedentes.
Ao atingir a cota, informar indisponibilidade e aguardar renovação ou decisão
humana. Assinatura não significa capacidade ilimitada; acompanhar cota,
latência e consumo junto com o custo mensal. Reserva local não reserva a cota
global compartilhada com outros usos do mesmo plano.

Jev e Laya foram sugeridos como candidatos. Seu papel proposto é classificar
uma consulta para auxiliar a escolha de rota; a resposta livre continua com
um modelo de conversa. O classificador não concede permissões, aceita
aprovações nem decide sozinho que um pedido sensível é seguro.

| Candidato | Evidência oficial consultada em 2026-10-11 | Posição neste incremento |
| --- | --- | --- |
| Jev / TypeSafe | Retorna decisões tipadas e probabilidades; é oferecido por API com preço por tokens | Referência de comparação; não habilitar a API sob demanda nesta fase. [Introdução](https://docs.typesafe.ai/introduction), [oferta oficial](https://typesafe.ai/) |
| Laya | Pesos Apache-2.0, execução local e variante multilíngue; não gera a resposta de chat | Priorizar avaliação local em português, ainda sem escolha definitiva. A model card registra limitações de precisão e calibração. [Model card](https://huggingface.co/convaiinnovations/laya), [repositório upstream](https://github.com/NandhaKishorM/laya) |

Preparar avaliação de sugestões de rota em paralelo às decisões humanas, sem
efeito operacional: poucas classes, casos em português, ambiguidades e pedidos
fora do escopo. Comparar com regras determinísticas e medir erros de
encaminhamento, abstenção, RAM/VRAM, CPU e latência nesta workstation. Uma
menção explícita a um agente já resolve o destino sem modelo classificador.
Somente adotar Laya onde ele acrescentar qualidade ou reduzir consumo total;
incluir seu próprio custo computacional e erros na comparação. Fixar revisão
de código/pesos e verificar artefatos antes do ensaio.

O [primeiro ensaio local](../pilots/README.md#laya-em-português) foi concluído
com Laya multilíngue fixado por revisão/hash, em ambiente temporário isolado.
Nos 32 casos sintéticos, acertou 8, contra 29 do baseline de regras; consumiu
2,28 GiB de pico RSS e teve mediana quente de 221,5 ms por consulta em CPU.
Não houve truncamento. Manter Laya fora da operação nesta configuração. Os
rótulos são provisórios e ambos erraram classificação de pedido fora do escopo;
nenhuma dessas medições valida autorização ou desempenho em tráfego real.

### Caminhos oficiais para usar a assinatura

O Codex CLI disponível nesta sessão reportou versão `0.160.0` e autenticação
ChatGPT em uma consulta de status sem revelar credenciais. Isso confirma o
método de login, não o plano, a cota restante ou elegibilidade de outras
integrações. O operador confirmou assinaturas ativas de ChatGPT/Codex, Claude
Pro/Max, Kimi, GLM/Z.ai e Google One Student. Os tiers exatos, limites atuais e
benefícios de CLI de cada plano ainda não foram auditados; não inferir esses
direitos apenas pela existência de uma assinatura no aplicativo.

- **Executor Codex local:** a documentação permite login ChatGPT para acesso
  por assinatura, e `codex exec` reutiliza a autenticação salva. É candidato
  ao primeiro ensaio supervisionado. [Autenticação](https://learn.chatgpt.com/docs/auth),
  [modo não interativo](https://learn.chatgpt.com/docs/non-interactive-mode).
- **Integração explícita do plano ChatGPT:** existe documentação para apps
  open-source e locais usando consentimento próprio e requisições elegíveis.
  Há também caminho de Codex por gateway em workspaces gerenciados. Verificar
  plano, consentimento, versão e compatibilidade antes de escolher. Não copiar
  tokens do Codex como atalho. [Uso do plano](https://developers.openai.com/siwc/token-sharing-open-source),
  [gateway](https://learn.chatgpt.com/docs/enterprise/sign-in-with-chatgpt-through-a-gateway).
- **Outros executores por assinatura:** avaliar com o plano efetivo e a
  interface oficial. Acesso no aplicativo/CLI não prova que a assinatura
  funciona em um SDK genérico ou proxy de modelos.

Limites específicos já identificados para as assinaturas informadas:

- Kimi Code restringe a assinatura ao uso pessoal interativo e exclui automação
  não interativa; não colocá-lo como worker desatendido deste atendimento.
  [Diretrizes oficiais](https://www.kimi.com/en/help/kimi-code/community-guidelines).
- GLM Coding Plan limita o benefício ao assinante e às ferramentas oficialmente
  suportadas. Verificar se o plano do operador é esse e se o caminho pretendido
  é admitido; não tratá-lo como backend HTTP genérico de uso multiusuário.
  [Política oficial](https://docs.z.ai/devpack/usage-policy).
- Google: confirmar o benefício concreto de Google One Student no Gemini CLI.
  O CLI oficial e o acesso direto ao Code Assist por software de terceiros com
  seu OAuth são caminhos diferentes; este último é vedado na documentação.
  [Termos do Gemini CLI](https://geminicli.com/docs/resources/tos-privacy/).

O transporte HTTP/API de uma ferramenta pode consumir uma franquia de plano;
isso é distinto de cobrança por uso. Registrar para cada rota o método de
autenticação, executor admitido, entitlement, cota e comportamento ao esgotá-la.

Um wrapper de CLI pode controlar admissão, duração e resultado da execução;
isso não comprova controle de cada inferência ou ferramenta interna. O caminho
institucional exige cobertura por chamada ou isenção registrada conforme a
ADR-0008. Uma rota de modelos por gateway também não governa automaticamente
MCPs, plugins, filesystem ou shell. A integração de assinatura ainda não foi
implementada no atendimento IRC.

O [executor local de desenvolvimento](../pilots/README.md#executor-codex-por-assinatura)
já completou uma consulta pela autenticação ChatGPT, com modelo solicitado
`gpt-6.1-sol`, pre/post-call e dois eventos correlacionados no Thalamus local.
A CLI informou 8.161 tokens de entrada (6.912 em cache) e 178 de saída em
9,653 segundos. O uso inclui o contexto nativo, além do prompt explícito de
2.191 bytes. Esta evidência valida uma consulta supervisionada por assinatura;
não comprova atendimento IRC implantado, economia contra o piloto anterior
ou mediação de cada inferência interna do executor.

## Papel do Thalamus e segundo incremento

O contrato arquitetural do Thalamus admite políticas de modelo/backend,
contexto, tokens, orçamento, risco e validação antes/depois da chamada. O
adaptador `BackendPort` executa a rota permitida; a organização das sessões e
missões permanece no dono do atendimento/Maestro. Comms mantém semântica,
correlação e apresentação das mensagens; Infra mantém transporte e operação.
Flight Deck continua dono de seu domínio e aprovações, não deste chat genérico.

O segundo incremento é um executor explicitamente integrado a esse contrato,
inicialmente com backend simulado e contagem verificável de chamadas. Deve
incluir contexto organizacional/tenant verificado para dados de tenant,
pre/post-gates, redação de segredos/PII e produção somente leitura por padrão.
Consulta documental continua sem acionar missões ou conceder autorização de
execução. O chat não substitui os contratos de aprovação e despacho.

Os documentos consultados estabelecem responsabilidades, não prontidão da
implantação. O piloto não chamou Thalamus e não existe, nesta evidência,
redirecionamento automático das chamadas internas do Codex desktop para ele.
Integrar um executor exige verificar interfaces implementadas,
autenticação, roteamento, telemetria, falhas e limites no caminho real.
O ensaio supervisionado local se enquadra na exceção de desenvolvimento/revisão
da ADR-0008, R9; essa exceção não define a arquitetura de um serviço
institucional permanente ou agendado.

O primeiro [ensaio isolado](../pilots/evidence/thalamus-probe.json) foi executado
com o servidor compilado da revisão `17fccd4`, sem features de backend,
política sintética e executor que retorna texto fixo. `/v1/decide` não gera os
IDs de auditoria; `/v1/pre-call` devolve a correlação usada por `/v1/post-call`.
Uma execução simulada produziu resultado `Valid` e dois eventos correlacionados.
Tenant desconhecido foi negado e post-call órfão recusado.

O servidor aceitou o pedido de backend desconhecido e orçamento maior que a
política; o probe recusou esses casos conferindo pedido e envelope antes do
executor. Essa recusa adicional pertence ao cliente. Revisão pendente e decisão
desconhecida foram simuladas nos testes de unidade; não foram produzidas pela
política JSON do servidor. A instância foi encerrada, sem auditoria persistente,
verificador de identidade ou chamada a provedor. O ensaio confirma o contrato
local observado, não prontidão institucional.

## Aceitação antes de qualquer novo ensaio ao vivo

1. Conta desconhecida, nick falsificado, canal errado, histórico, metadados
   ausentes/inválidos e sessão inativa/expirada não iniciam trabalho.
2. O mesmo ID de mensagem após reconexão ou reinício conserva a correlação;
   conteúdo/conta conflitantes são recusados. Texto igual com ID novo é distinto.
3. Dois consumidores têm um único claim vencedor. Tokens antigos não publicam.
   Se uma chamada externa puder ter ocorrido antes do crash, reconciliar pelo
   ID de invocação ou marcar incerto; não repetir cegamente para recuperar a lease.
4. Resposta humana não contém UUID/epoch de transporte, preserva Unicode e
   palavras quando possível, respeita os limites e nunca vira nova pergunta.
   A suíte existente de comunicação entre máquinas continua passando.
5. Parada, limites de sessão e orçamento impedem novas chamadas. Backend
   simulado confirma ausência de fan-out/retries ocultos e trilha de uso por pedido.
6. Uma nova rodada no Halloy comprova a pergunta simplificada e a resposta
   visível; não basta fila vazia. Medir tempo de atendimento separadamente do
   redesenho do cliente e continuar o diagnóstico de estabilidade prolongada.

## Base da proposta

- Evidência observada: [proveniência e resultado do piloto](HUMAN-FLIGHTDECK-PILOT.md).
- Implementação atual: `agent_protocol.py`, `agent_store.py` e
  `agent_transport.py` em `infra/irc/bots/strategos-irc-bot/src/strategos_irc_bot`.
- Thalamus: `BOUNDARIES.md`, `ARCHITECTURE.md`,
  `docs/02-architecture/target-architecture.md` e
  `docs/02-architecture/agentgateway-and-data-plane.md`, revisão
  `17fccd4edc19c7333be5dfcf7d5e0d5b7346a9d6` de `thalamus-core`.
- Governance: ADR-0008, regras R3 a R8 e R10, revisão
  `3681e6952ff4dd7f61c1a7b35327e10fc8d84996` de `rbx-governance`.
- A proposta de camadas e isolamento continua em
  `rbx-comms/docs/architecture/sovereign-communications-roadmap.md`. Este
  contrato não ratifica decisões corporativas ou mapeamento RBX/Merovelis.
