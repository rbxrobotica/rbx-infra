# Runbook IRC RBX

Todos os comandos partem da raiz de `rbx-infra`. Eles não alteram firewall, DNS
ou proxy reverso.

## 1. Preparar o desktop

```bash
./infra/irc/scripts/bootstrap-weechat-mint.sh
rbx-irc
```

Desanexe do tmux com `Ctrl-b d` e reanexe com `tmux attach -t irc`. Os modelos
copiados para `~/.config/rbx-irc` devem ser revisados e colados dentro do WeeChat.

## 2. Implantar ZNC na Corbetti

```bash
RBX_IRC_HOST=corbetti ./infra/irc/scripts/bootstrap-znc-corbetti.sh
```

O script cria `/srv/rbx/irc/znc`, preserva `.env`/config existentes, sobe apenas
`rbx-znc` e verifica o bind. Na primeira inicialização, abra o WebAdmin pelo
túnel e troque imediatamente a senha inicial.

Se o SSH automatizado falhar, copie `infra/irc/znc` para o host e execute:

```bash
sudo install -d -m 0750 /srv/rbx/irc/znc
sudo install -d -m 0700 /srv/rbx/irc/znc/config
sudo chown 1000:1000 /srv/rbx/irc/znc/config
cd /srv/rbx/irc/znc
sudo cp -n env.example .env
sudo chmod 0600 .env
sudo docker compose --env-file .env -f compose.yaml config --quiet
sudo docker compose --env-file .env -f compose.yaml up -d znc
sudo ss -ltnp | grep ':6501'
```

## 3. Abrir o túnel ZNC

```bash
RBX_IRC_HOST=corbetti LOCAL_ZNC_PORT=6501 REMOTE_ZNC_PORT=6501 \
  ./infra/irc/scripts/open-znc-tunnel.sh
```

Mantenha o processo aberto. O listener local será `127.0.0.1:6501`; configure
WeeChat conforme `weechat/servers-znc.commands.example`. Dentro desse túnel, TLS
entre WeeChat e loopback é opcional. Pela rede, TLS é obrigatório.

## 4. Preparar o Ergo

Primeiro distribua os arquivos de base; a primeira execução para com instruções
se não existir `ircd.yaml` final:

```bash
RBX_IRC_HOST=corbetti ./infra/irc/scripts/bootstrap-ergo-internal.sh
```

Na Corbetti:

```bash
cd /srv/rbx/irc/ergo
sudo cp -n ircd.yaml.example config/ircd.yaml
sudo cp -n ergo.motd.example config/ergo.motd
sudo docker run --rm -it --entrypoint /ircd-bin/ergo \
  ghcr.io/ergochat/ergo:v2.19.1 genpasswd
sudoedit config/ircd.yaml
sudo install -d -m 0700 config/tls
# Provisione config/tls/fullchain.pem e config/tls/privkey.pem fora do Git.
sudo chmod 0600 config/ircd.yaml config/tls/privkey.pem
```

Substitua o hash de demonstração, não uma senha em texto claro. Para a primeira
conta, altere temporariamente apenas este bloco:

```yaml
accounts:
  require-sasl:
    enabled: true
    exempted:
      - localhost
```

Rode o bootstrap com a trava explícita, conecte pelo túnel local e crie as contas
como descrito abaixo:

```bash
RBX_IRC_ACCOUNT_BOOTSTRAP=1 RBX_IRC_HOST=corbetti \
  ./infra/irc/scripts/bootstrap-ergo-internal.sh
```

Depois restaure `exempted: []`, rode o bootstrap novamente e confirme que uma
conexão sem SASL é recusada. Não deixe a exceção de bootstrap ativa.

Para acesso exclusivamente por SSH:

```bash
ssh -N -T -L 6667:127.0.0.1:6667 corbetti
```

Para VPN, defina `ERGO_TLS_BIND_ADDRESS` como o IP privado/Tailscale na `.env` e
mantenha plaintext em loopback. Não prossiga sem revisão do firewall de nuvem.

## 5. Provisionar contas e canais

Conecte localmente durante a janela controlada descrita acima, autentique o oper
com `/OPER admin <senha>` e consulte `/msg NickServ HELP SAREGISTER`. Crie as
contas iniciais com senha temporária, por exemplo:

```text
/msg NickServ SAREGISTER <conta> <senha-temporaria>
```

Depois, conecte cada cliente com SASL. Crie e registre os canais abaixo como a
conta fundadora:

```text
/join #rbx-founder
/msg ChanServ REGISTER #rbx-founder
/mode #rbx-founder +si
/msg ChanServ AMODE #rbx-founder +o <conta-autorizada>
```

Repita para `#strategos`, `#missions`, `#deploys`, `#incidents`,
`#briefing-btc`, `#robson`, `#creative-ops` e `#ledger`. Salas efêmeras seguem
`#mission-<mission-id>`. Use `AMODE +v` para membros que precisam entrar em uma
sala invite-only sem poderes de moderação.

## 6. Iniciar o bot

```bash
cd infra/irc/bots/strategos-irc-bot
cp config.example.yaml config.yaml
chmod 0600 config.yaml
export STRATEGOS_IRC_PASSWORD='<senha-da-conta-do-bot>'
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/strategos-irc-bot --config config.yaml
```

Use conta dedicada, sem oper, e mantenha `strategos.enabled: false`. A lista
`auth.allowed_accounts` é comparada à tag de conta autenticada do IRCv3.

Para execução persistente na Corbetti, crie `bot.env` conforme o README do bot e
execute:

```bash
RBX_IRC_HOST=corbetti ./infra/irc/scripts/bootstrap-strategos-bot-corbetti.sh
```

## 7. Saúde e diagnóstico

```bash
RBX_IRC_HOST=corbetti ./infra/irc/scripts/irc-healthcheck.sh
```

Se um serviço não iniciar:

```bash
ssh corbetti 'cd /srv/rbx/irc/znc && sudo docker compose ps && sudo docker compose logs --tail=100 znc'
ssh corbetti 'cd /srv/rbx/irc/ergo && sudo docker compose ps && sudo docker compose logs --tail=100 ergo'
```

Falha SASL normalmente indica conta inexistente, senha incorreta ou cliente sem
SASL PLAIN/SCRAM. Falha TLS normalmente indica arquivo ausente, permissão da
chave ou nome do certificado incompatível.

## 8. Backup e restauração

```bash
RBX_IRC_HOST=corbetti ./infra/irc/scripts/backup-znc.sh
RBX_IRC_HOST=corbetti ./infra/irc/scripts/backup-ergo.sh
```

Os scripts param e reiniciam brevemente o respectivo container. Para restaurar,
primeiro faça rollback do container, preserve o diretório atual com outro nome e
extraia o arquivo escolhido como root no `/` (o tar contém caminho relativo a
`/srv/rbx/irc`). Exemplo para ZNC:

```bash
RBX_IRC_CONFIRM=rollback-znc ./infra/irc/scripts/rollback-znc.sh
ssh corbetti
sudo mv /srv/rbx/irc/znc/config /srv/rbx/irc/znc/config.before-restore
sudo tar -xzf /srv/rbx/irc/backups/znc-<timestamp>.tar.gz -C /
exit
RBX_IRC_HOST=corbetti ./infra/irc/scripts/bootstrap-znc-corbetti.sh
```

Valide conteúdo e permissões antes de remover `config.before-restore`.

## 9. Rollback

```bash
RBX_IRC_CONFIRM=rollback-znc RBX_IRC_HOST=corbetti \
  ./infra/irc/scripts/rollback-znc.sh
RBX_IRC_CONFIRM=rollback-ergo RBX_IRC_HOST=corbetti \
  ./infra/irc/scripts/rollback-ergo.sh
RBX_IRC_CONFIRM=rollback-strategos-bot RBX_IRC_HOST=corbetti \
  ./infra/irc/scripts/rollback-strategos-bot.sh
```

Os comandos param e removem somente o container correspondente. Configuração,
dados, `.env`, certificados, imagens e backups permanecem no host.
