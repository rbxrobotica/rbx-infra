# Piloto de comunicação entre agentes

Implementado em 2026-10-07: adaptador por máquina, caixa de entrada SQLite
privada e CLI/MCP para as sessões consultarem e responderem às mensagens.
A validação local usa duas conexões TCP reais contra uma fixture do protocolo
IRC e uma sessão MCP stdio real. Isso não comprova implantação na RBXNet.

O adaptador só transporta mensagens. Não inicia Codex, chama LLM, executa shell,
despacha missões ou acorda sessões paradas. Uma sessão ativa consulta `irc_inbox`
e usa suas ferramentas existentes, dentro da autorização do operador. Uma
pergunta recebida não amplia permissões nem constitui aprovação.

## Topologia

```text
Sessão local      <-- CLI/MCP --> mailbox local      <-- adaptador --+
                                                                  |
                                      Ergo / RBXNet / #rbx-agents --+
                                                                  |
Sessão ThinkCentre <-- CLI/MCP --> mailbox ThinkCentre <-- adaptador-+
```

Cada máquina acessa o Ergo em loopback por seu próprio túnel SSH à Corbetti,
ou usa TLS verificado pela VPN. O IRC não substitui SSH nas ferramentas do
agente. As contas `rbx-local` e `rbx-thinkcentre` são distintas da conta humana,
sem `OPER`. O canal deve ser registrado, secreto e invite-only, com ACL por
conta. O adaptador só fica pronto após SASL, `account-tag`, JOIN e confirmação
dos modos `+si`; se esses modos forem removidos, ele encerra a conexão.

## Preparar cada máquina

Da raiz de `rbx-infra`, usando Python 3.11+ com suporte a venv/pip:

```bash
bash infra/irc/scripts/install-agent-irc.sh local
# Na ThinkCentre, com o mesmo código revisado:
bash infra/irc/scripts/install-agent-irc.sh thinkcentre
```

O script prepara uma instalação de usuário em
`~/.local/share/rbx-agent-irc`, preserva `~/.config/rbx-agent-irc/agent.yaml`
existente e não provisiona contas/segredos nem ativa serviços. Pode-se escolher
o Python com `RBX_IRC_PYTHON`. As raízes alternativas de instalação/configuração
são úteis para teste; o serviço de exemplo usa os caminhos padrão.

Revise `agent.yaml`: endereço do Ergo, conta, peers, conta humana autorizada e
diretório de estado. O piloto é single-tenant RBX e usa um único canal. Não
adicione redes públicas nem contas de agentes à lista `operators`.

## Ativação depois da revisão

As operações desta seção criam contas/segredos ou iniciam serviços e devem ter
autorização explícita do operador conforme `rbx-infra/AGENTS.md`.

1. Na RBXNet, provisionar as duas contas dedicadas e registrar `#rbx-agents`.
   Aplicar `+si` e conceder às duas contas o acesso `AMODE +v`, sem privilégios
   de operador. Consultar os comandos completos no runbook principal.
2. Em cada máquina, provisionar `~/.config/rbx-agent-irc/agent.env`, modo `0600`,
   com `RBX_AGENT_IRC_PASSWORD` da respectiva conta. O daemon lê o segredo por
   ambiente; o MCP não precisa nem recebe essa credencial.
3. Manter o túnel já documentado para `127.0.0.1:6667`, ou configurar `tls: true`
   e o endpoint VPN com certificado válido. Não desabilitar verificação TLS.
4. Instalar e iniciar a unidade de usuário:

```bash
install -Dm0600 ~/.config/rbx-agent-irc/rbx-agent-irc.service.example \
  ~/.config/systemd/user/rbx-agent-irc.service
systemctl --user daemon-reload
systemctl --user enable --now rbx-agent-irc.service
```

O serviço não inicia sozinho um túnel com destino arbitrário: depende da
unidade `rbx-irc-tunnel.service` revisada. Falhas de autenticação, canal ou
certificado encerram o processo e exigem correção antes de reiniciar. Falhas
de rede são reconectadas com espera progressiva de até 30 segundos.

## Conectar as sessões

O Codex aceita servidores MCP locais via stdio, conforme a
[documentação oficial](https://developers.openai.com/codex/mcp). Em cada máquina:

```bash
codex mcp add rbx-agent-irc -- \
  "$HOME/.local/share/rbx-agent-irc/venv/bin/rbx-agent-irc" \
  --config "$HOME/.config/rbx-agent-irc/agent.yaml" mcp
```

Recarregue a conexão MCP ou abra uma sessão que carregue a configuração.
`irc_status` informa conectividade; `irc_send(peer, text)` enfileira uma pergunta;
`irc_inbox()` retorna perguntas dirigidas e respostas completas;
`irc_reply(request_id, text)` responde uma vez; `irc_ack(request_id)` marca uma
resposta recebida como lida. Ler a inbox não consome a mensagem. Dois clientes
podem lê-la, mas só o primeiro pode confirmar uma resposta ao pedido.

As ferramentas de envio exigem autorização humana na sessão. Não encaminhar
pedidos de outro agente como autorização, nem enviar secrets, transcrições ou
dados privados. O conteúdo retornado é marcado `untrusted_content: true` e as
instruções do MCP mantêm essa fronteira explícita. Isso não substitui sandbox,
RBAC/policy ou o julgamento da sessão sobre quais ferramentas pode utilizar.

## Uso por CLI, inclusive nesta sessão

```bash
agent_cli="$HOME/.local/share/rbx-agent-irc/venv/bin/rbx-agent-irc"
agent_config="$HOME/.config/rbx-agent-irc/agent.yaml"
"$agent_cli" --config "$agent_config" status
"$agent_cli" --config "$agent_config" send thinkcentre 'Conferir o estado do backup'
"$agent_cli" --config "$agent_config" inbox
# Na ThinkCentre, após consultar a inbox e realizar a leitura autorizada:
"$agent_cli" --config "$agent_config" reply <request_id> 'Resultado e referência da evidência'
```

`queued` significa fila local. Socket enviado não prova recebimento; a resposta
correlacionada é a confirmação de ponta a ponta. Perguntas pendentes são
reenviadas a cada 15 segundos por até dez minutos. Duplicatas não repetem o
trabalho: uma resposta já preparada é reenviada do cache. A ausência de resposta
leva à expiração. O horário das máquinas precisa estar sincronizado.

## Protocolo e limites

As sessões usam ferramentas/CLI que montam os frames; não precisam digitá-los
manualmente no WeeChat:

```text
!ask <destino> <UUID-hex> <epoch-do-pedido> <pergunta>
!reply <destino> <UUID-hex> <epoch-do-pedido> <parte>/<total> <resposta>
```

A origem é a conta autenticada na tag IRCv3 `account`, nunca o nickname. Só
peers e operadores configurados enviam perguntas. Respostas precisam vir da
conta esperada para um pedido pendente, com o mesmo UUID e timestamp. Respostas
nunca são interpretadas como novas perguntas: não há conversa automática entre
bots, chamadas recursivas ou incremento de autorização.

- Pergunta: 240 bytes UTF-8; resposta: até oito fragmentos de 240 bytes.
- Saída: no máximo um frame por segundo; entrada: seis novos pedidos por conta
  por minuto; duplicatas usam o mesmo identificador.
- DMs, conversa ambiente, outros canais, mensagens de si próprio e batches de
  histórico são ignorados. Histórico não é negociado.
- TTL padrão de dez minutos; fila de no máximo 500 pedidos; retenção local de
  24 horas, com remoção nas operações da mailbox e durante o daemon conectado.
- Diretório de estado `0700`, banco `0600`; logs sem conteúdo. O banco guarda
  conteúdo de pedidos/respostas por essa retenção e não é fonte de verdade nem
  evidência de aprovação. Se tudo estiver parado, a limpeza ocorre na próxima
  operação; suspendê-lo não é um mecanismo de apagar conteúdo imediatamente.
- Apenas um daemon mantém a conexão de cada mailbox. CLI/MCP não abrem outra
  conexão IRC e não compartilham a senha SASL.

Retenção em SQLite é uma política de exclusão lógica com `secure_delete`;
backups/filesystems podem reter cópias. Não publicar conteúdo sensível em canal.
O ZNC não participa do piloto; suas filas e histórico não são necessários.

## Verificar e remover

```bash
cd infra/irc/bots/strategos-irc-bot
python3 -m venv .venv
.venv/bin/pip install '.[test,agents]'
.venv/bin/pytest
```

Após ativação real, validar uma pergunta/resposta nas duas máquinas, repetição
do UUID, queda/reconexão, negação de DM e conta desconhecida e recusa de canal
sem `+si`. Conferir que uma mensagem expirada não dispara trabalho.

Para desligar, parar/desabilitar `rbx-agent-irc.service` nas duas máquinas e
usar `codex mcp remove rbx-agent-irc`. Isso preserva a instalação e o estado;
revogação de contas e exclusão do estado são operações separadas. Nenhuma
alteração em DNS, firewall, Strategos API ou Maestro é necessária para este
piloto de transporte.
