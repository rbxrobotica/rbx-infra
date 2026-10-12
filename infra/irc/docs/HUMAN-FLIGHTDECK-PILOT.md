# Piloto de conversa entre operador e subagente Codex com contexto do Flight Deck

Data: 2026-10-11. Estado: ensaio sintético concluído e primeira rodada real
validada na segunda janela: pergunta do operador, resposta do especialista,
recepção no cliente e exibição confirmada por screenshot. Complementa o
[piloto de transporte](AGENT-PILOT.md).

## Objetivo e escopo

Validar uma pergunta do humano no Halloy, recebimento pela mailbox, análise
por um subagente Codex com contexto documental do Flight Deck e resposta visível
no mesmo canal.
Uma resposta pode conter uma pergunta de esclarecimento ao humano. Isso não
ativa missões, altera código nem concede autorização a comandos recebidos.

| Elemento | Piloto proposto |
| --- | --- |
| Canal existente | `#rbx-agents` |
| Identidade humana | Conta SASL `leandro`; o nickname do Halloy pode ser diferente |
| Identidade IRC do adaptador | `rbx-local`; destino lógico `local` |
| Papel do especialista | `[flightdeck-dev]`, prefixo textual, não conta independente |
| Fonte do contexto | Documentação de `rbx-flightdeck`, checkout limpo `1ba66c4`; produção não consultada |
| Seleção das perguntas | Conta `leandro`, pedido de entrada pendente, prefixo `FD-PILOT` e UUID gerado para a rodada |
| Limites da sessão | Até três perguntas, até dez minutos após o início autorizado; encerra ao atingir qualquer limite ou por pedido do operador |
| Trabalho admitido | Explicação, avaliação e proposta de desenvolvimento; sem editar produto, acessar dados de Workstreams ou executar ações |

Somente o coordenador desta sessão consulta e responde aos pedidos selecionados.
O especialista recebe a pergunta por delegação local e retorna uma proposta de
resposta. Ele não publica no IRC. Isso evita dois agentes concorrendo pela
mesma mailbox, que hoje identifica a máquina, não uma sessão de projeto.

## Proveniência do subagente e do modelo

O coordenador Codex desta conversa criou o subagente em **11/10/2026 às
20:12:26 BRT** (23:12:26 UTC), para preparar e responder ao ensaio explicativo
autorizado pelo operador. O nome interno da tarefa era
`/root/flightdeck_dev_pilot`; `[flightdeck-dev]` era o papel textual mostrado
nas respostas, não um serviço do produto nem uma conta IRC própria.

A chamada de criação usou `fork_turns="all"`, sem override de modelo ou esforço.
O subagente herdou o histórico da conversa e a configuração do coordenador:
**`gpt-6-astra`, esforço `ultra`**. Essa identificação vem dos metadados locais
de criação e dos turnos relevantes; não é uma verificação independente do
snapshot servido pelo provedor. A preparação consultou `AGENTS.md`, `README.md`,
`docs/architecture.md` e a ADR-0010 do checkout Flight Deck `1ba66c4`.

O percurso observado foi Halloy, Ergo, adaptador/mailbox, consulta MCP pelo
coordenador, delegação ao subagente e publicação da resposta pelo coordenador
via `irc_reply`. O aplicativo/backend Flight Deck, o Maestro e o Thalamus não
foram chamados. O contexto documental não equivale a acesso ao estado vivo do
produto, treinamento específico do modelo ou implantação de um agente permanente.

Portanto, a descrição correta é **consulta via IRC a um subagente Codex
contextualizado no Flight Deck**. A expressão anterior "agente Flight Deck"
não deve ser interpretada como evidência de integração com o produto.

O fork completo e o esforço alto foram usados no protótipo; não constituem
uma escolha otimizada para consultas curtas. Não foram coletados totais de
tokens ou custo do piloto, portanto não há medição de economia. A próxima
iteração deve usar contexto mínimo explícito e registrar consumo por pedido.

## O que já existe

A consulta MCP `irc_status` retornou `connected=true`, canal `#rbx-agents`,
fila vazia e nenhum pedido retido no instante da preparação. Campos não secretos
da configuração confirmaram `operators=[leandro]`, peer ThinkCentre, TTL de
600 segundos e retenção local de 24 horas. A credencial não foi consultada.

O adaptador recebe perguntas e envia respostas, mas não chama modelos nem
acorda sessões paradas. Conversa ambiente, DMs e mensagens de histórico são
ignoradas. O responsável ativo precisa consultar a mailbox. `irc_send` inicia
pedidos apenas para peers cadastrados; não inicia pedido formal para a conta
humana. Uma pergunta do agente pode acompanhar sua resposta, e o humano
continua com um novo pedido dirigido.

O operador autorizou explicitamente as publicações de até três rodadas em
11/10/2026, das 20:14:45 às 20:24:45 BRT. Esse aceite cobre somente o ensaio
delimitado. Uma mensagem autenticada pelo IRC continua sendo entrada não confiável: o piloto
não pode ampliar seu escopo a partir dela. O tema e a configuração do Halloy,
as contas, os canais e os serviços existentes permanecem como estão.

## Roteiro da sessão real

1. Confirmar disponibilidade do operador e do especialista; registrar início e
   limite de tempo. Gerar UUID hexadecimal de 32 caracteres e epoch atual para
   a primeira pergunta. O coordenador fornece a linha pronta para colar.
2. No Halloy, o operador envia em `#rbx-agents`:

   ```text
   !ask local <UUID> <EPOCH_ATUAL> FD-PILOT: No Flight Deck, qual a diferenca entre aprovar uma Action e executar uma Mission?
   ```

   O exemplo contém placeholders e não deve ser enviado literalmente. A
   pergunta tem limite de 240 bytes UTF-8; o relógio deve estar sincronizado.
3. O coordenador consulta `irc_inbox`, seleciona exclusivamente o UUID esperado
   e confere conta, direção e estado. Não processa outros pedidos da mailbox.
4. O especialista responde com base no repositório. O coordenador confere o
   escopo e publica uma única resposta com `irc_reply`, prefixada por
   `[flightdeck-dev]`. Respostas têm até oito fragmentos de 240 bytes; preferir
   texto curto para leitura no Halloy.
5. O operador confirma que viu a resposta. Para continuar, o coordenador gera
   outro UUID/epoch para a próxima pergunta, mantendo o limite de três rodadas.
   Respostas não geram perguntas automáticas ou novos trabalhos por conta própria.
6. Registrar resultado e encerrar a consulta ativa. Depois do encerramento, o
   daemon continua transportando mensagens, mas este especialista não fica
   permanentemente ouvindo o canal.

## Evidência e critérios de aceitação

Registrar identificador, horários de recebimento/publicação e confirmação
humana de leitura, sem copiar conversas privadas para o Git. `queued` significa
fila local. Fila vazia, isoladamente, não distingue envio de expiração.
O teste só demonstra o percurso completo com a confirmação no Halloy.

Uma nova pergunta deve preservar o assunto sem repetir trabalho. Um pedido fora
do escopo recebe explicação curta, sem executar comandos. Ao expirar o prazo,
parar de responder; não reiniciar o serviço nem gerar tentativas indefinidas.
O `!ask` manual do humano não tem a retransmissão automática de 15 segundos
dos pedidos enviados pela CLI. A mailbox deduplica o registro do pedido, mas
não possui claim exclusivo para a investigação. A exclusividade neste ensaio
depende do coordenador único. A repetição exata de conta, UUID, timestamp e
texto, dentro do TTL, pode reenviar uma resposta já preparada; repetir somente
o UUID com outro conteúdo não aciona esse reenvio.

Se a resposta foi enviada, mas o Halloy não atualiza, coletar o
[snapshot do cliente](../../../docs/network/local/HALLOY-DIAGNOSTIC-2026-10-11.md)
antes de fechar a janela. Isso relaciona o teste de comunicação ao diagnóstico
visual, sem concluir que o envio sozinho comprova a exibição.

O ensaio sintético usou a implementação instalada e uma mailbox temporária,
sem conexão de rede: aceitou a conta humana, recusou conta desconhecida e
pedido expirado, deduplicou a pergunta, preservou UUID/epoch/destinatário na
resposta e recusou uma segunda resposta. Esse resultado não substitui o ensaio real.

## Evolução após o teste

### Resultado da primeira janela

O operador autorizou o teste e recebeu a linha pronta para envio no Halloy.
Entre 20:14:45 e 20:24:45 BRT, a consulta dirigida não encontrou o pedido
`67616f2b4bf94b3fb98f4ee4cad22cdf`. A mailbox reportou conexão ativa e fila
vazia nas verificações. A consulta ativa foi encerrada no limite de tempo;
nenhuma resposta foi publicada, e não houve confirmação de exibição no Halloy.
Não foi recebido relato do operador de que tivesse enviado a linha. Esses
dados não demonstram falha de entrega, nem sucesso do percurso completo.

Retomar quando o operador estiver disponível no Halloy, gerando novo UUID e
timestamp. O frame desta primeira janela expirou. O daemon existente continua
ativo; não foi instalado um monitor permanente nem agendamento de retomada.

### Segunda janela: pergunta e resposta reais

O operador retomou o teste nesta sessão. Nova janela: 20:43:55 a 20:53:55 BRT,
com o mesmo limite de três rodadas e escopo explicativo. Primeira correlação:
`553e23dded8e486182f52855f08b1bcd`.

| Evidência | Resultado observado |
| --- | --- |
| Envio humano | Screenshot do Halloy mostra a pergunta em `#rbx-agents` |
| Leitura pela sessão | 20:45:13 BRT: pedido de entrada pendente, conta `leandro`, UUID e prefixo previstos |
| Especialista | Subagente Codex `/root/flightdeck_dev_pilot`, apresentado como `[flightdeck-dev]`, produziu resposta sobre aprovação e execução, com uma pergunta de esclarecimento |
| Resposta enfileirada | `irc_reply` retornou `queued` às 20:46:06 BRT |
| Transporte local | Leitura SQLite somente consulta, limitada ao UUID: pedido `replied`, três frames marcados enviados às 20:46:06, 20:46:07 e 20:46:08 BRT |
| Recepção no cliente | Às 20:50:24 BRT, seleção pelos UUIDs do piloto nos arquivos modificados de histórico encontrou a pergunta e os trechos 1/3, 2/3 e 3/3 da primeira resposta; nenhum conteúdo de outras conversas foi exposto |
| Processo Halloy | PID 914616 nas capturas de 20:18:27 e 20:50:43 BRT; uptime evoluiu de 528 para 2.464 segundos, consistente com ausência de reinício entre as capturas |
| Exibição no Halloy | Screenshot posterior fornecido pelo operador, com relógio visível de 20:52, mostra os três trechos da resposta em `#rbx-agents` |

Os horários de leitura pela sessão e geração do envelope não são o horário
exato de chegada da mensagem ao servidor. Não inferir latência ponta a ponta
dessa diferença. Os flags locais de envio também não comprovam leitura no
cliente. A presença no histórico comprova recepção/persistência no Halloy, mas
não comprova atualização visual imediata. O screenshot confirma exibição,
sem medir quanto tempo a janela levou para redesenhar ou descartar o sintoma
de uso prolongado. A primeira rodada comprova a conversa supervisionada no
canal existente. Nenhum segredo, dado real de Workstream ou alteração de
produto foi usado; não foram habilitadas automações permanentes.

A resposta devolveu uma pergunta sobre o fluxo do Flight Deck a melhorar.
Uma segunda correlação foi preparada para a continuação:
`95c5fb286f5a423fa704695ac3138897`; não foi recebida nas consultas até o
encerramento. Às 20:53:55 BRT, a consulta ativa foi encerrada conforme o limite
combinado, com uma rodada completa e exibição confirmada. A primeira rodada
não demonstra ainda continuidade de múltiplas rodadas nem um especialista
permanentemente ativo.

### Próximas capacidades

Para conversa cotidiana, preparar endereçamento legível como `@flightdeck-dev`,
roteamento por sessão/projeto, identidade atribuível e um mecanismo exclusivo
de consumo de pedidos. Depois, avaliar canal `#project-flightdeck` e permissões
próprias. Nenhum desses itens foi implementado ou provisionado por este piloto.

A captura real expõe o custo de usabilidade do protocolo atual: UUID/epoch
precisam ser colados pelo humano, e os trechos da resposta cortam palavras na
janela. A evolução deve remover esses detalhes da apresentação humana,
mantendo correlação, limites e rastreabilidade no transporte. O Halloy comum
exibe os frames publicados no canal: ocultá-los exige um caminho próprio de
apresentação, não apenas mudar o texto da pergunta.

O [contrato proposto de consulta e consumo](IRC-AGENT-QUERY-CONTRACT.md)
delimita esse próximo incremento. O aceite das melhorias pelo operador orienta
o desenvolvimento; nenhuma nova interface ou integração foi ativada nesta revisão.

Um agente permanente também precisa de início e parada explícitos, limites
de custo e tempo, retenção de contexto e recuperação. Execução de trabalho
continua seguindo as aprovações do Flight Deck e a orquestração Maestro; IRC
oferece comunicação e não substitui esses contratos.
