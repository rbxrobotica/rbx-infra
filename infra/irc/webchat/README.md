# Webchat privado no Mint

O webchat usa The Lounge em modo privado, dentro de um container Podman
rootless. O HTTP escuta somente em `127.0.0.1:9000`. A rede do container é a do
host exclusivamente para alcançar o túnel local `127.0.0.1:6667`; por isso a
configuração de bind em `config.js` é uma fronteira obrigatória.

Instale ou reconcilie sem sobrescrever dados existentes:

```bash
./infra/irc/scripts/bootstrap-webchat-mint.sh
```

Depois abra <http://127.0.0.1:9000>. O usuário inicial é `leandro`; a senha
aleatória fica apenas em
`~/.config/rbx-irc/webchat-initial-credentials` com modo `0600`. O arquivo deve
ser removido depois de guardar a senha em um cofre e trocá-la pela interface.

Na primeira conexão à RBXNet, informe a credencial SASL individual do Ergo. Ela
não é copiada de Halloy, WeeChat ou qualquer outro cofre. O servidor, a porta e
TLS ficam bloqueados em `127.0.0.1:6667` sem TLS porque esse trecho atravessa o
túnel SSH local. The Lounge não é publicado na LAN, na VPN ou na internet.

O modelo desabilita upload, preview remoto e armazenamento adicional de
mensagens. O histórico autorizado permanece no Ergo. Para ver estado e logs:

```bash
systemctl --user status rbx-irc-webchat.service
journalctl --user -u rbx-irc-webchat.service --since today
```

Rollback preservando usuário e configuração (o Quadlet é renomeado para
`.container.disabled`, portanto não volta no próximo login):

```bash
RBX_IRC_CONFIRM=rollback-webchat \
  ./infra/irc/scripts/rollback-webchat-mint.sh
```
