# strategos-irc-bot

Adaptador IRCv3 somente leitura para o Ergo interno. Ele usa SASL PLAIN,
solicita `account-tag`, ignora mensagens privadas e autoriza pela conta
autenticada. Conversa normal na sala é ignorada: somente mensagens iniciadas
por `!` nos canais configurados são processadas. Não há função de shell ou
cliente de API real.

## Executar

```bash
cp config.example.yaml config.yaml
chmod 0600 config.yaml
export STRATEGOS_IRC_PASSWORD='<senha-da-conta-dedicada>'
python3 -m venv .venv
.venv/bin/pip install '.[test]'
.venv/bin/pytest
.venv/bin/strategos-irc-bot --config config.yaml
```

Use `tls: false` somente ao conectar por loopback dentro de um túnel SSH. Para
acesso por VPN/rede, aponte `server` para `:6697`, ative `tls: true` e mantenha
`tls_verify: true`.

## Comandos

- `!help`
- `!status`
- `!mission list`
- `!mission status <id>`
- `!deploy status`
- `!cost today`
- `!risk open`
- `!mission approve <id>` — stub sem efeito; bloqueado em read-only

Respostas são mockadas enquanto `strategos.enabled` for `false`. Definir essa
opção como `true` faz o processo recusar a inicialização até que um cliente real
seja implementado e revisado. Uma implementação futura deve manter a interface
`StrategosClient`, aplicar RBAC/policy e escrever evidência no ledger.

O piloto atual é single-tenant (`RBX`). Antes de habilitar uma API real, cada
canal deve possuir um tenant imutável e todos os métodos do cliente devem exigir
o contexto `(conta, canal, tenant)`. Isso impede que o modo Holding transforme
uma sala em atalho acidental para dados de outro tenant. O valor atual vem de
`strategos.tenant_id` e é `rbx` no arquivo de exemplo.

Não use `MAESTRO_DASHBOARD_KEY` no bot: a superfície atual também permite
admitir missões e criar leases. A integração real permanece bloqueada até haver
uma credencial de serviço e rotas estritamente read-only.

O processo não registra mensagens ou senhas. Nunca dê oper à conta do bot e não
adicione canais públicos à configuração.

## Serviço persistente na Corbetti

O bootstrap de infraestrutura copia o código para `/srv/rbx/irc/bots`, preserva
`config.yaml`/`bot.env` e instala uma unidade systemd com `DynamicUser` e
hardening. O secret deve existir previamente, fora do Git:

```bash
sudo install -d -m 0700 /srv/rbx/irc/bots/strategos-irc-bot
sudoedit /srv/rbx/irc/bots/strategos-irc-bot/bot.env
# STRATEGOS_IRC_PASSWORD=<senha-da-conta-dedicada>
sudo chmod 0600 /srv/rbx/irc/bots/strategos-irc-bot/bot.env

RBX_IRC_HOST=corbetti ../../scripts/bootstrap-strategos-bot-corbetti.sh
```

Rollback preservando código/configuração:

```bash
RBX_IRC_CONFIRM=rollback-strategos-bot ../../scripts/rollback-strategos-bot.sh
```
