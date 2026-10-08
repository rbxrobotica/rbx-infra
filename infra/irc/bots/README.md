# Bots IRC RBX

Bots neste diretório são adaptadores internos para `RBXNet`. Eles não entram em
redes públicas, não executam shell e não substituem Strategos, Maestro, Git ou o
ledger como fonte de verdade.

O primeiro adaptador é [strategos-irc-bot](strategos-irc-bot/README.md), somente
leitura e com integração real desabilitada.

O mesmo pacote oferece `rbx-agent-irc`: um adaptador de transporte separado
para as caixas de entrada de sessões locais e da ThinkCentre, com CLI/MCP,
correlação e respostas explícitas. Ele não executa shell nem inicia agentes.
Veja o [piloto e instalação](../docs/AGENT-PILOT.md).
