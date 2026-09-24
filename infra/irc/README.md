# Infraestrutura IRC da RBX

Base reversível para presença IRC pessoal, comunicação interna e uma futura
interface ChatOps do Strategos. O IRC é uma camada de interação: Strategos,
Maestro, Git e o ledger continuam sendo os sistemas canônicos.

## Estado

- Artefatos preparados; nenhum serviço é implantado automaticamente pelo Git.
- ZNC e Ergo publicam portas somente em `127.0.0.1` por padrão.
- O bot é somente leitura, não executa shell e não chama APIs reais.
- Segredos, chaves e certificados devem ser provisionados fora do repositório.

A auditoria de 2026-09-24 encontrou Linux Mint 22.3 XFCE no desktop, `tmux`
instalado, WeeChat ausente e Podman Compose disponível. A Corbetti responde pelo
alias SSH `corbetti`, é Ubuntu 24.04, tem Docker Compose e Tailscale, e não tinha
listeners ou containers IRC. O UFW está ativo com política de entrada `deny` e
permite publicamente apenas SSH em `22/tcp`; nenhuma regra foi alterada.

## Componentes

| Componente | Uso | Exposição padrão |
| --- | --- | --- |
| WeeChat + tmux | Cliente local | Nenhuma |
| ZNC | Presença persistente em redes públicas | `127.0.0.1:6501` na Corbetti |
| Ergo | Rede interna `RBXNet` | `127.0.0.1:6667` e `127.0.0.1:6697` |
| strategos-irc-bot | Adaptador ChatOps somente leitura | Conexão de saída ao Ergo |

## Início rápido

```bash
./infra/irc/scripts/bootstrap-weechat-mint.sh
RBX_IRC_HOST=corbetti ./infra/irc/scripts/bootstrap-znc-corbetti.sh
./infra/irc/scripts/open-znc-tunnel.sh
RBX_IRC_HOST=corbetti ./infra/irc/scripts/irc-healthcheck.sh
```

O Ergo exige uma configuração final revisada, credencial de operador com hash e
certificados externos antes de subir. Consulte [docs/RUNBOOK.md](docs/RUNBOOK.md)
para o fluxo completo e [docs/SECURITY.md](docs/SECURITY.md) para os limites de
segurança.

## Portas

- `6501/tcp`: ZNC, somente loopback; acessado com túnel SSH.
- `6667/tcp`: Ergo sem TLS, somente loopback; permitido apenas dentro de túnel.
- `6697/tcp`: Ergo com TLS, loopback por padrão; pode ser ligado explicitamente
  a um endereço privado/VPN.

Os scripts não alteram UFW, nftables, iptables, firewall de nuvem, DNS ou proxy
reverso.
