# Infraestrutura IRC da RBX

Base reversível para presença IRC pessoal, comunicação interna e uma futura
interface ChatOps do Strategos. O IRC é uma camada de interação: Strategos,
Maestro, Git e o ledger continuam sendo os sistemas canônicos.

## Estado

- O Git não implanta serviços automaticamente. A auditoria operacional de
  2026-11-18 confirmou a implantação feita fora do Git descrita abaixo.
- ZNC e Ergo publicam portas somente em `127.0.0.1` por padrão.
- O bot é somente leitura, não executa shell e não chama APIs reais.
- Segredos, chaves e certificados devem ser provisionados fora do repositório.

A auditoria de 2026-11-18 encontrou Linux Mint 22.3 XFCE no desktop com `tmux`,
WeeChat, Halloy e Podman rootless. O túnel systemd local está ativo em
`127.0.0.1:6667` e o agente `rbx-thinkcentre` está autenticado na RBXNet. A
Corbetti responde pelo alias SSH `corbetti`, é Ubuntu 24.04 e executa ZNC, Ergo e
o adaptador Strategos. ZNC e Ergo estão limitados a loopback em `6501`, `6667` e
`6697`; nenhuma regra de firewall foi alterada.

O adaptador Strategos implantado na Corbetti ainda é uma cópia anterior ao
reset do timeout pós-conexão presente neste repositório. Ele autentica, mas
reconecta aproximadamente a cada 15 segundos. Corrigir essa divergência exige
um deploy separado; o healthcheck a sinaliza sem modificar o host.

O [piloto entre agentes](docs/AGENT-PILOT.md) acrescenta uma mailbox por máquina
e ferramentas CLI/MCP para perguntas e respostas explícitas no canal privado
`#rbx-agents`. O transporte não executa ferramentas nem chama modelos. A
validação local não confirma implantação nas máquinas reais.

The agent mailbox also supports the RBX ParadeDB/PostgreSQL storage standard.
See the [offline migration runbook](docs/POSTGRES-MAILBOX-RUNBOOK.md) and
[destination preflight](docs/POSTGRES-DESTINATION-PREFLIGHT.md). This prepared
backend does not migrate an installed mailbox or enable human queries on merge.

## Componentes

| Componente | Uso | Exposição padrão |
| --- | --- | --- |
| WeeChat + tmux | Cliente local | Nenhuma |
| The Lounge | Webchat privado no Mint | `127.0.0.1:9000` no Mint |
| ZNC | Presença persistente em redes públicas | `127.0.0.1:6501` na Corbetti |
| Ergo | Rede interna `RBXNet` | `127.0.0.1:6667` e `127.0.0.1:6697` |
| strategos-irc-bot | Adaptador ChatOps somente leitura | Conexão de saída ao Ergo |

## Início rápido

```bash
./infra/irc/scripts/bootstrap-weechat-mint.sh
./infra/irc/scripts/bootstrap-webchat-mint.sh
RBX_IRC_HOST=corbetti ./infra/irc/scripts/bootstrap-znc-corbetti.sh
./infra/irc/scripts/open-znc-tunnel.sh
RBX_IRC_HOST=corbetti ./infra/irc/scripts/irc-healthcheck.sh
```

O Ergo exige uma configuração final revisada, credencial de operador com hash e
certificados externos antes de subir. Consulte [docs/RUNBOOK.md](docs/RUNBOOK.md)
para o fluxo completo e [docs/SECURITY.md](docs/SECURITY.md) para os limites de
segurança. A fronteira de identidade e o plano para Android, ZITADEL, tenants e
OpenClaw estão em [docs/IDENTITY-CHATOPS.md](docs/IDENTITY-CHATOPS.md).

## Portas

- `6501/tcp`: ZNC, somente loopback; acessado com túnel SSH.
- `6667/tcp`: Ergo sem TLS, somente loopback; permitido apenas dentro de túnel.
- `6697/tcp`: Ergo com TLS, loopback por padrão; pode ser ligado explicitamente
  a um endereço privado/VPN.
- `9000/tcp`: The Lounge HTTP, somente loopback no Mint; não publicar por proxy.

Os scripts não alteram UFW, nftables, iptables, firewall de nuvem, DNS ou proxy
reverso.
