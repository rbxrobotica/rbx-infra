# Consultas humanas supervisionadas pelo IRC

Estado: implementado para validação em desenvolvimento, ainda não instalado.
Escopo: o alias `flightdeck-dev` usa a conta do adaptador, por exemplo
`rbx-local`, para consultar documentação do projeto `rbx-flightdeck`.

O operador envia no Halloy:

```text
@flightdeck-dev Qual a diferença entre aprovar uma Action e executar uma Mission?
```

O adaptador entrega a pergunta à fila persistente. Um consumidor autorizado reserva
a pergunta, prepara a resposta e a conclui com seu token de fencing. O canal
recebe texto legível:

```text
[flightdeck-dev | Codex | consulta documental] leandro: (1/1) Aprovar registra a decisão; executar realiza o trabalho autorizado. [ref FD-0001]
```

A referência permite consultar conta, sessão, revisão documental, modelo
declarado, pergunta, resposta e estado da entrega. O modelo é metadado
informado pelo coordenador, não comprovação de inferência. O código não chama
Flight Deck, Maestro, Codex ou Thalamus. Não inicia processos para responder,
não aumenta cotas e não escolhe modelos por fallback. O consumidor desta fase
é uma sessão Codex supervisionada, autorizada separadamente pelo operador.

## Limites e confiança

- Configuração `human_queries: true` e sessão local explícita são necessárias.
  O valor padrão é `false`; os exemplos existentes continuam desativados.
- Apenas `flightdeck-dev` / `rbx-flightdeck`, conta humana cadastrada em
  `operators` e o único canal configurado. Contas de peers e do próprio bot
  não são humanas. Nickname não concede acesso.
- Até três perguntas em até 600 segundos, também limitado ao TTL configurado.
  A última pergunta fecha a admissão, mas pode ser respondida até o prazo.
  Parada pelo operador encerra a admissão e impede novas publicações.
- Perguntas de até 240 bytes UTF-8; respostas de até oito partes e 1.920
  bytes de conteúdo, respeitando também o espaço ocupado pela apresentação.
  A fragmentação preserva Unicode e procura limites de palavras. O transporte
  mantém no máximo um frame de aplicação por segundo.
- `account-tag`, SASL, `message-tags` e `server-time` precisam ser negociados;
  o canal deve permanecer secreto e apenas por convite (`+si`). Sem isso, o
  transporte falha fechado. Sem `msgid` ou horário válido, a pergunta é ignorada.
- Aceita horário UTC com milissegundos, idade máxima de 30 segundos e tolerância
  futura de cinco segundos, sempre dentro da sessão. DMs, batches de histórico,
  mensagens ambientes e menções fora do início são ignorados.
- IDs opacos são sensíveis a maiúsculas. Repetição do mesmo ID conserva a
  correlação sem repetir trabalho ou resposta; conteúdo ou conta conflitantes
  são recusados. Mesmo texto com novo ID pode ser uma nova pergunta.
- Uma sessão fixa seu consumidor na primeira reserva; apenas uma consulta fica
  em atendimento. Claim exige proprietário, token de fencing e lease válida.
  A lease dura no máximo 600 segundos, limitada ao fim da sessão.
- Lease perdida ou falha declarada incerta bloqueia novos claims da sessão.
  Não há recuperação automática nem segunda chamada para descobrir o resultado.
  O coordenador deve reconciliar o possível consumo antes de abrir outra sessão.
- Mudança da lista de operadores ou dos peers invalida sessões cujo humano
  perdeu autorização. Claims e publicação voltam a verificar esses limites.
- Resposta é persistida antes de publicar. Cada frame tem uma única tentativa
  automática. Crash, erro de escrita ou perda da confirmação deixam a entrega
  incerta; frames restantes não são reenviados automaticamente nessa condição.
  `socket_sent` registra sucesso de escrita/drain, não exibição ou leitura.

O processo local e os arquivos privados do usuário são a fronteira de confiança
desta fase. `owner` é um identificador de correlação, não uma credencial contra
outros processos do mesmo usuário. A CLI de sessão não é exposta no MCP, e a
mensagem IRC não concede autorização para usar ferramentas ou executar tarefas.
Texto recebido continua marcado como conteúdo não confiável.

As regras de metadados seguem as especificações de
[IDs de mensagem](https://ircv3.net/specs/extensions/message-ids) e
[horário do servidor](https://ircv3.net/specs/extensions/server-time).
Negociar capabilities não comprova que a instalação efetiva do Ergo envia todos
os metadados; a verificação por mensagem permanece obrigatória.

## Operação local depois da instalação autorizada

Os comandos abaixo pressupõem a configuração local revisada e habilitada.
Não são instruções para executar automaticamente ao fazer merge. Não usar
senhas ou conteúdo confidencial como argumentos ou respostas.

```bash
rbx-agent-irc --config /caminho/privado/agent.yaml human session-open \
  --account leandro --project rbx-flightdeck \
  --revision <sha-completo-da-documentacao> --model <modelo-da-sessao>

rbx-agent-irc --config /caminho/privado/agent.yaml human inbox
rbx-agent-irc --config /caminho/privado/agent.yaml human claim <request_id> \
  --owner <identificador-do-consumidor> --lease-seconds 120

rbx-agent-irc --config /caminho/privado/agent.yaml human complete <request_id> \
  'Resposta documental com fonte e limites.' \
  --owner <identificador-do-consumidor> --fence <token-recebido-no-claim>

rbx-agent-irc --config /caminho/privado/agent.yaml human reference FD-0001
rbx-agent-irc --config /caminho/privado/agent.yaml human status
rbx-agent-irc --config /caminho/privado/agent.yaml human session-close <session_id>
```

`inbox` apenas consulta; `claim` reserva. A conclusão persiste uma resposta,
mas precisa do daemon conectado para enviá-la. Para registrar falha, usar
`human fail <request_id> 'Motivo sem segredos' --owner ... --fence ...`.
Acrescentar `--uncertain` quando uma chamada puder ter ocorrido sem resultado
confirmado. Não reutilizar automaticamente o pedido.

## Estado, migração e retorno

### PostgreSQL mailbox preparation

The adapter supports an explicitly selected PostgreSQL backend, following
the RBX ParadeDB standard. Existing SQLite installations retain their behavior
until a controlled cutover; no fallback or automatic migration is provided.
Claims, deduplication and response state use the selected database. Trusted
local administration and the human-session permission model are unchanged.

Read the [storage and migration runbook](POSTGRES-MAILBOX-RUNBOOK.md) and
[destination preflight](POSTGRES-DESTINATION-PREFLIGHT.md) before installation.
Schema initialization, a frozen source snapshot, an empty destination,
verified import and explicit reconciliation precede activation. The initializer
creates both peer and human tables; `human_queries: false` still prevents human
admission. An import is held against runtime I/O until release checks pass.
The destination, backup/restore and network checks remain unverified for live use.

The SQLite rollback procedure below does not apply after PostgreSQL has
accepted new writes. Preserve both states and follow the PostgreSQL runbook.

### Existing SQLite installations

No backend SQLite, `human_queries: false` não cria tabelas novas. Ao habilitar, o código adiciona
somente `human_meta`, `human_sessions`, `human_requests` e `human_outbox` à
mailbox existente, com índice por sessão. Não altera colunas/tabelas de peers
nem transforma pedidos legados em consultas humanas. A migração é transacional
e idempotente. Mantém diretório 0700, SQLite 0600 e limpeza pela retenção local
configurada. Retenção local não altera a retenção do servidor IRC.

A identidade humana também fixa o endpoint `server` e a opção `tls` em `meta`.
Reabrir esse estado com outro endpoint falha fechado, mesmo se conta e canal
tiverem o mesmo nome. A comparação é conservadora: migrar porta/alias do mesmo
servidor exige reconciliação e estado isolado novo, sem reaproveitar sessões
pendentes. Não editar essa identidade manualmente para contornar a checagem.

Antes da instalação real, registrar o dono e caminho da mailbox, revisão do
código/configuração, permissões, backup consistente de SQLite, destino e
ensaio de restauração. Como o código abre o banco automaticamente, fazer essa
etapa antes da primeira execução com opt-in. Usar cópia privada ou API de
backup SQLite com o serviço quiescente; nunca adicionar o banco ao Git.

Para retorno: encerrar a sessão, parar o transporte, preservar estado e
evidências, remover a chave `human_queries` da configuração ao usar o binário
anterior, restaurar o pacote anterior e iniciar o fluxo legado. O loader antigo
rejeita a chave nova mesmo se ela estiver `false`. As tabelas adicionais podem
permanecer: o leitor legado as ignora. Restaurar um snapshot anterior também
apaga a memória de deduplicação posterior ao snapshot; manter a entrada humana
desligada e reconciliar perguntas antes de novo ensaio. Não apagar a mailbox
para resolver erro ou repetir chamadas incertas.

## Evidência e próximo passo

A suíte cobre autenticação por conta, metadados ausentes, sessão inativa,
limites, replay, dois consumidores concorrentes, fences, lease perdida,
parada/deadline antes de publicação, migração e leitura pelo código legado.
A fixture TCP loopback percorre CAP/SASL/canal privado, reconexão e resposta
legível usando um consumidor documental sintético. Nenhum teste invoca modelos
ou envia mensagens à RBXNet.

Além da suíte, o `Mailbox` extraído da revisão anterior
`4628b8334fa62488f43f16223b7556e9a4eef7fc` reabriu uma mailbox temporária após a
migração e completou a correlação de pergunta/resposta entre peers, preservando
o pedido humano. Isso valida compatibilidade do schema; não substitui backup e
ensaio de retorno do serviço instalado.

O ensaio seguinte deve verificar a instalação preparada, capacidades do Ergo e
pergunta/resposta visíveis no Halloy. Depois, conectar a fila ao executor com
política Thalamus, contexto mínimo, reserva e reconciliação de consumo, limites
por sessão e identificação do executor real. A CLI desta fase não implementa
orçamento de tokens nem prova controle de inferências internas do Codex.
O diagnóstico de atualização prolongada do Halloy permanece separado.
