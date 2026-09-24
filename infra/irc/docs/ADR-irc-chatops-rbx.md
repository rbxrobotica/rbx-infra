# ADR: IRC e ChatOps leve para RBX

## Status

**Proposed** — 2026-09-24

## Contexto

A RBX precisa de presença persistente em redes IRC públicas, salas internas de
missão e uma interface operacional de baixo custo. A solução deve preservar
soberania, auditabilidade e reversibilidade sem duplicar o estado de Strategos,
Maestro, Git ou do ledger.

## Decisão

Adotar três camadas separadas:

1. WeeChat dentro de tmux no desktop Linux Mint para uso pessoal.
2. ZNC em container na Corbetti para presença persistente nas redes públicas,
   acessível somente por túnel SSH.
3. Ergo em container como `RBXNet`, restrito a loopback ou VPN, com contas/SASL
   obrigatórios, registro fechado e canais privados.

Um bot Python mínimo atua como adaptador somente leitura para Strategos. Ele
autentica por conta IRC, não executa shell e mantém integrações reais desligadas
até existirem contratos de API, RBAC, policy engine e evidência no ledger.

## Alternativas consideradas

- Clientes GUI: mais simples visualmente, mas menos adequados a sessões remotas
  persistentes e automação leve.
- The Lounge: boa experiência web, porém adiciona uma superfície HTTP e gestão
  de sessão que não é necessária nesta fase.
- Matrix: federação e clientes ricos, com custo operacional muito maior.
- Slack e Discord: convenientes, mas dependem de SaaS externo e reduzem
  soberania/controle de dados.
- Mattermost: soberano, porém mais pesado que a necessidade atual.

## Consequências positivas

- Presença assíncrona com consumo pequeno de recursos.
- Componentes independentes, com dados persistentes e rollback explícito.
- Rede interna sem exposição pública padrão.
- Fronteira clara: IRC transporta interação, mas não é fonte de verdade.

## Riscos

- IRC não oferece criptografia ponta a ponta; operadores do servidor podem ver
  mensagens.
- Logs e histórico podem capturar dados operacionais sensíveis.
- Uma conta comprometida pode enviar comandos válidos ao bot.
- Tags flutuantes de container podem introduzir mudanças não revisadas.
- Canais podem ser configurados incorretamente após a criação.

## Mitigações

- TLS fora de túnel local, bind loopback por padrão e acesso via SSH/Tailscale.
- SASL obrigatório, registro fechado, canais `+si` e ACLs por conta.
- Retenção curta, ausência de logs de conteúdo e backup protegido.
- Bot somente leitura; aprovações continuam como stub sem efeito.
- Ergo fixado em versão corrigida; ZNC exige backup e revisão antes de atualizar.
- Healthcheck detecta binds públicos e configurações ausentes.

## Reavaliação

Reavaliar após um piloto interno ou antes de permitir qualquer comando mutável,
listener fora de rede privada, bridge, cliente web ou integração com LLM.
