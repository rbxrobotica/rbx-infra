# WeeChat + tmux

```bash
../scripts/bootstrap-weechat-mint.sh
rbx-irc
```

O helper cria ou reanexa a sessão tmux `irc` e inicia o WeeChat. Desanexe com
`Ctrl-b d`; volte com `tmux attach -t irc`. Encerre o WeeChat com `/quit`.

Os arquivos `.commands.example` são modelos para colar na barra de comandos do
WeeChat depois de substituir placeholders. Não versione `sec.conf` nem senhas.

Para ZNC, execute `open-znc-tunnel.sh` em outro terminal antes de conectar. O
tráfego WeeChat–`127.0.0.1:6501` pode ser plaintext porque permanece dentro do
túnel SSH. Qualquer acesso ao ZNC pela rede deve usar TLS com verificação de
certificado.

Para o Ergo interno via túnel, use `127.0.0.1:6667` sem TLS apenas dentro do SSH.
Por VPN ou outra rede, conecte à porta `6697` com TLS e SASL obrigatórios.
