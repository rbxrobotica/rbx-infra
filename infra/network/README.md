# Registro da rede local RBX

Este diretório reúne o inventário documental da rede local. Não é um inventário
Ansible, não configura equipamentos e não concede acesso. Os nomes de host
existentes foram preservados; os identificadores `rbx-*` são propostas de
inventário, ainda não configuradas como hostname, DNS ou alias SSH.

- [Arquitetura e decisões locais](../../docs/network/local/README.md).
- [Evidência de 2026-10-11](../../docs/network/local/EVIDENCE-2026-10-11.md).
- [Registro dos equipamentos](registry/hosts.yaml).
- [Mesh Headscale existente](../../docs/adr/ADR-0011-headscale-sovereign-mesh-on-eagle.md).
- [Piloto IRC e limites da mailbox](../irc/docs/AGENT-PILOT.md).

## Como ler o registro

Cada equipamento separa três classes de informação:

| Campo | Significado |
|---|---|
| `observed` | Resultado da inspeção de 2026-10-11, salvo data específica indicada; sujeito a mudança após a coleta |
| `reported` | Informação fornecida pelo operador, ainda sem inspeção do equipamento |
| `proposed` | Papel ou identificação sugerida; não representa configuração aplicada |

Endereços LAN observados não são reservas DHCP nem endereços estáticos
garantidos. O gateway `192.168.0.1` foi observado, mas o modelo ZTE ZXHN F6600P
vem da identificação feita pelo operador. O painel ainda não permitiu verificar
reservas DHCP, isolamento de convidados ou regras efetivas do roteador.

No diagnóstico dos desktops, `ENABLED=no` no arquivo do UFW não equivale a uma
auditoria completa do firewall. A aceitação de uma chave de host no SSH também
não equivale à autorização de login: a conexão workstation → ThinkCentre foi
autenticada por chave; o sentido inverso teve autenticação recusada. Nenhuma
nova chave ou permissão foi concedida nesta etapa.

## Responsabilidade por configuração

- **`rbx-infra`** mantém inventário, decisões, matriz de acesso e configuração
  dos serviços cloud Headscale/IRC. Este registro descreve os desktops sem
  incluí-los implicitamente nos playbooks de servidores.
- **`rbx-workstation`** concentra a configuração dos hosts. Seu bootstrap
  completo atual é destinado a VPS Ubuntu; no Mint existe um bootstrap restrito
  do cliente Tailscale. Adaptar e revisar o hardening para os desktops é uma
  etapa posterior, não uma capacidade aplicada por este registro.
- **Operador humano** decide proprietários, acesso administrativo e concessões.
  Identidades de agentes e integrações devem ser separadas por finalidade; um
  identificador de dispositivo não concede privilégios de operador.
- **Projetos e sistemas de acompanhamento** guardam especificações, tarefas e
  resultados duráveis. O IRC transporta conversa e pedidos; sua mailbox não
  executa comandos, acorda agentes ou constitui autorização/ledger.

## Manutenção

Atualize a evidência e a data quando uma inspeção mudar um fato observado.
Promova uma proposta a configuração efetiva somente após aplicação e teste
registrados. Preserve aliases e identidades atuais durante a transição.

Mantenha senhas, tokens, chaves, caminhos privados de credenciais e identificadores
desnecessários fora deste inventário. O equipamento Enforce continua externo ao
domínio administrativo RBX; sua presença na rede não autoriza gerenciá-lo.
