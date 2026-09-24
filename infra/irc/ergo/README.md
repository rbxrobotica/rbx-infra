# Ergo interno (`RBXNet`)

O compose usa a imagem oficial fixada em `v2.19.1`, persiste `/ircd` e mantém
plaintext e TLS em loopback por padrão. O listener interno do container usa
`:6667`/`:6697`; a fronteira de exposição é o bind do host no compose.

## Preparação obrigatória

```bash
cp ircd.yaml.example ircd.yaml
docker run --rm -it --entrypoint /ircd-bin/ergo \
  ghcr.io/ergochat/ergo:v2.19.1 genpasswd
# Substitua o hash de demonstração em ircd.yaml.
install -d -m 0700 config/tls
# Provisione fullchain.pem e privkey.pem fora do Git.
```

Na Corbetti, os caminhos finais são
`/srv/rbx/irc/ergo/config/ircd.yaml` e
`/srv/rbx/irc/ergo/config/tls/{fullchain.pem,privkey.pem}`. A chave privada deve
ter modo `0600`.

```bash
RBX_IRC_HOST=corbetti ../scripts/bootstrap-ergo-internal.sh
```

O bootstrap recusa iniciar se faltar a configuração final, se o hash conhecido
de demonstração ainda estiver presente ou se faltarem certificados.
Cada execução validada recria somente o container `rbx-ergo`, aplicando mudanças
do arquivo montado sem alterar dados persistentes.

Como o estado final não isenta nem loopback do SASL, a primeira conta exige uma
janela controlada: adicione temporariamente `localhost` a
`accounts.require-sasl.exempted`, suba o serviço, use `/OPER` e `SAREGISTER`, e
restaure imediatamente `exempted: []`. A implantação só está concluída depois
de reiniciar e confirmar que uma conexão sem SASL é recusada.

Para publicar TLS numa VPN, altere apenas `ERGO_TLS_BIND_ADDRESS` para o endereço
privado da Corbetti. A porta plaintext permanece forçada a `127.0.0.1` pelo
compose. O bootstrap aceita somente loopback, RFC1918 ou a faixa Tailscale/CGNAT
`100.64.0.0/10`; ele recusa wildcard e IPv4 público.

Os canais operacionais são criados após a conta fundadora ser provisionada;
veja o runbook para registro e ACLs. A configuração torna novos canais secretos,
invite-only e exclusivos para criação por operadores.
