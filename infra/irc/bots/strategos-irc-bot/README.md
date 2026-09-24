# strategos-irc-bot

Adaptador IRCv3 somente leitura para o Ergo interno. Ele usa SASL PLAIN,
solicita `account-tag`, ignora mensagens privadas e autoriza pela conta
autenticada. Não há função de shell ou cliente de API real.

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

O processo não registra mensagens ou senhas. Nunca dê oper à conta do bot e não
adicione canais públicos à configuração.
