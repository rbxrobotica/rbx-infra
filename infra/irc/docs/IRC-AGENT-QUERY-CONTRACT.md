# Consulta humana a agentes por IRC: contrato proposto

Estado: **proposta de implementação**, 2026-10-11. O operador concordou com
as melhorias de experiência após o [piloto supervisionado](HUMAN-FLIGHTDECK-PILOT.md).
Este documento prepara o incremento seguinte; não instala um atendimento
permanente, altera o protocolo em execução ou ativa uma rota de modelos.

## Resultado pretendido

O humano envia, no canal autorizado:

```text
@flightdeck-dev Qual a diferença entre aprovar uma Action e executar uma Mission?
```

O adaptador apresenta uma resposta como:

```text
[flightdeck-dev | Codex | consulta documental] leandro: Aprovar uma Action registra a decisão e seus limites. Executar uma Mission realiza o trabalho autorizado. Fonte: Flight Deck, revisão <sha>. [ref FD-0042]
```

São exemplos de apresentação, não comandos já disponíveis. A identificação
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

Os estados e campos acima são requisitos, não uma descrição do schema atual.
O adaptador existente deduplica pedidos e aceita uma única resposta, mas não
possui claim exclusivo para a investigação. A implementação precisa preservar
a compatibilidade das mailboxes e testar migração/retorno antes de instalação.

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
Integrar um executor futuro exige verificar interfaces implementadas,
autenticação, roteamento, telemetria, falhas e limites no caminho real.
O ensaio supervisionado local se enquadra na exceção de desenvolvimento/revisão
da ADR-0008, R9; essa exceção não define a arquitetura de um serviço
institucional permanente ou agendado.

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
