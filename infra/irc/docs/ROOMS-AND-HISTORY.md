# Salas operacionais e histórico

O catálogo explícito em `../rooms.json` define as salas iniciais da RBX:
agentes, workflows, notificações, manutenção, incidentes, projeto Strategos e
bancada ThinkCentre. Outras salas podem representar grupos, mesas, comitês e
conselhos; sua criação e seus membros precisam ser revisados no catálogo.

Cada sala é registrada, secreta e invite-only (`+si`). `leandro` é a conta
fundadora; membros recebem apenas `AMODE +v`, sem `OPER` ou administração da
sala. Cada integração recebe sua própria conta e somente as salas necessárias.
O catálogo não concede acesso a contas futuras automaticamente. O transporte
de pedidos entre agentes continua limitado a `#rbx-agents`; conversa ambiente e
histórico não disparam ações nem ampliam a autorização da sessão.

## Registro contínuo

O próprio Ergo registra mensagens e eventos continuamente em SQLite, inclusive
quando os clientes estão desconectados. Não depende de uma sessão de agente
acordada. O arquivo `config/ergo_history.db` fica no volume persistente, em um
diretório `0700`, com arquivos de banco `0600`. O backup existente para o Ergo
para o serviço durante a cópia e inclui esse diretório.

A retenção é de **30 dias**, não uma promessa de histórico ilimitado. DMs e
canais não registrados não são arquivados. Salas registradas usam `opt-in`:
somente as salas aprovadas recebem `ChanServ SET #sala HISTORY on`. Dados
anteriores à ativação ou já expirados não podem ser reconstruídos. Backups
privados podem preservar conteúdo além da retenção do banco ativo.

O padrão global de consulta permanece `join-time`. Nas salas deste catálogo,
`ChanServ SET #sala QUERY-CUTOFF none` permite a membros autorizados consultar
todo o histórico ainda retido, incluindo mensagens anteriores à entrada.
Conceder acesso à sala concede acesso a esse histórico; retire o acesso por
conta e remova conexões existentes ao revogar um membro. A sala anuncia a
retenção no tópico. Eventos JOIN/PART/TOPIC também podem ser registrados.

## Preparar e aplicar

`scripts/configure-room-history.py` gera uma configuração privada (`0600`) sem
mostrar segredos, alterar contas, implantar ou reiniciar serviços:

```bash
python3 infra/irc/scripts/configure-room-history.py \
  --config /srv/rbx/irc/ergo/config/ircd.yaml \
  --rooms infra/irc/rooms.json \
  --output /srv/rbx/irc/ergo/config/ircd.history-candidate.yaml
```

O script mantém listeners, TLS, SASL, opers e privilégios existentes. Recusa
migração implícita de outro backend e não sobrescreve uma saída existente.
Antes de aplicar, guarde uma cópia privada da configuração, valide o candidato
com a imagem fixada do Ergo e confirme espaço em disco. Provisionar salas,
contas, segredos e aplicar a configuração exige autorização do operador.

Como a conta fundadora autenticada, com OPER para criação/registro:

```text
/join #sala
/msg ChanServ REGISTER #sala
/mode #sala +si
/msg ChanServ AMODE #sala +v conta-autorizada
/msg ChanServ SET #sala HISTORY on
/msg ChanServ SET #sala QUERY-CUTOFF none
/msg ChanServ SET #sala STORE-EVENTS all
```

Valide SASL, acesso negado de conta sem ACL, modos, recuperação de uma mensagem
por `CHATHISTORY` e recuperação da mesma mensagem depois de reiniciar o Ergo.
Use IDs de mensagens para deduplicar ao recuperar histórico. O banco deve
continuar privado, e logs de diagnóstico não devem imprimir conteúdo.

## Visualização no Strategos

Esta mudança prepara o transporte e a retenção. **O painel de salas no
Strategos ainda não está implementado.** O conector deverá ser uma conta
dedicada sem OPER, com uma lista explícita de salas. No Strategos, um serviço
RPC autenticado verifica tenant e autorização da sala no servidor antes de
fornecer histórico paginado e eventos em tempo real. A credencial IRC fica no
servidor e nunca no navegador.

O conector negocia `draft/chathistory`, `message-tags`, `server-time` e `batch`
para recuperar histórico com `msgid`, e mantém
uma conexão para eventos atuais. Histórico e eventos são mesclados pelo mesmo
ID; reconexões retomam um cursor, e lacunas/expiração aparecem no painel. A UI
mostra o período realmente disponível, horário, conta de origem, tópico e
estado da conexão. Não apresenta uma lista parcial como a totalidade da sala.
Nenhuma mensagem em uma sala executa comandos automaticamente. Workflows de
manutenção continuam sujeitos às permissões, runbooks e aprovações existentes.

Referência do backend fixado:
[Ergo 2.19.1 — histórico persistente](https://github.com/ergochat/ergo/blob/v2.19.1/docs/MANUAL.md#persistent-history).
