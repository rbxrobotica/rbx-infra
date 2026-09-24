# ZNC na Corbetti

O compose persiste `/config` no host e publica a porta apenas em loopback. Não
adicione proxy público ou regra de firewall para `6501`.

```bash
RBX_IRC_HOST=corbetti ../scripts/bootstrap-znc-corbetti.sh
../scripts/open-znc-tunnel.sh
```

Na primeira inicialização, a imagem LinuxServer pode criar a conta `admin` com
senha inicial `admin`. Abra `http://127.0.0.1:6501` somente através do túnel,
troque essa senha imediatamente e configure as redes. Desative o WebAdmin após
o bootstrap se não precisar dele continuamente.

O `.env` real fica na Corbetti, com modo `0600`, e não deve ser versionado. Antes
de atualizar a imagem flutuante do ZNC, crie backup e registre o digest aprovado.

Backup e remoção reversível do container:

```bash
RBX_IRC_HOST=corbetti ../scripts/backup-znc.sh
RBX_IRC_CONFIRM=rollback-znc RBX_IRC_HOST=corbetti ../scripts/rollback-znc.sh
```

O rollback preserva `/srv/rbx/irc/znc/config` e os backups.
