# Identidade e fronteira ChatOps

## Estado atual

A RBXNet é um piloto interno single-tenant. O Ergo autentica contas locais por
SASL e exige conta antes de concluir a conexão. Essas credenciais são uma ponte
de bootstrap: ainda não representam login Google ou identidade ZITADEL.

As salas `+si` são o plano de conversa. O bot:

- lê somente comandos que começam com `!` nos canais configurados;
- ignora conversa ambiente e mensagens privadas;
- autoriza pela tag IRCv3 `account`, nunca pelo nickname;
- não executa shell, deploy, ferramentas mutáveis ou chamadas a LLM;
- mantém `strategos.enabled: false` enquanto não houver API autenticada.

Para reuniões sensíveis, prefira um canal registrado `+si` com ACL explícita a
mensagens privadas. IRC não oferece criptografia ponta a ponta: o operador do
servidor continua dentro da fronteira de confiança. Não conecte o ZNC à RBXNet
sensível até buffers e retenção serem revisados; o histórico efêmero do Ergo
foi substituído pelo histórico persistente de 30 dias nas salas aprovadas,
conforme [ROOMS-AND-HISTORY.md](ROOMS-AND-HISTORY.md) e a
[evidência de 2026-10-08](EVIDENCE-2026-10-08.md). DMs não são arquivadas no
servidor; cópias locais nos clientes exigem tratamento próprio.

O piloto `rbx-agent-irc` de 2026-10-07 usa contas dedicadas por máquina e um
único canal RBX. Ele oferece transporte e mailbox, sem executar ferramentas,
integrar uma API ou acordar sessões. A entrada de agentes é consulta explícita
por CLI/MCP; mensagens não concedem autorização. Instalação, retenção e critérios
de aceitação estão em [AGENT-PILOT.md](AGENT-PILOT.md).

## Android, Google e ZITADEL

O padrão mobile de Strategos, Robson e demais produtos RBX está **em aberto**,
conforme esclarecimento do operador em 2026-10-11. Tauri e Wails v3 são
candidatos, não decisões aprovadas. O APK atual do Strategos abre a interface
web no navegador do sistema e não define esse padrão corporativo.

Se um produto assumir diretamente o fluxo OIDC como app nativo, ele será um
cliente público, sem client secret: login pelo navegador do sistema e
Authorization Code com PKCE, usando ZITADEL e seus provedores federados.
Uma entrada que apenas abre o site mantém o fluxo web/BFF existente.
A documentação oficial do ZITADEL recomenda PKCE para aplicações nativas:

- <https://zitadel.com/docs/guides/integrate/login/oidc/oauth-recommended-flows>
- <https://zitadel.com/docs/guides/integrate/login/oidc/login-users>

### Revisão de 2026-10-11: cliente IRC dedicado nos smartphones

O operador concordou explicitamente em revisar a proibição de senha própria
para cliente nativo. Para o piloto operacional, passa a ser admitida uma
credencial IRC persistente **individual por aparelho**, inclusive no POCO,
sujeita aos critérios abaixo. Isso substitui a proibição geral anterior para
esse uso; não escolhe o framework dos produtos RBX nem incorpora senha IRC
compartilhada em APK, WebView ou frontend.

- Conta humana própria por aparelho, distinta da conta fundadora, das contas
  de agentes e das contas de outros servidores IRC; sem OPER ou administração
  de salas. O apelido visual não substitui a identidade SASL.
- Senha aleatória exclusiva, entregue por procedimento privado, nunca em Git,
  chats ou logs. Verificar armazenamento, exportação e backup do cliente antes
  de colocar a credencial em uso; não presumir uso do Android Keystore.
- Lista explícita de salas por conta, com ciência de que a entrada permite
  consultar o histórico ainda retido dessas salas. Nenhuma ACL existente é
  ampliada automaticamente pela revisão documental.
- Transporte privado aprovado e TLS com certificado validado para o acesso
  móvel. Não reutilizar o loopback dos desktops como endpoint dos telefones.
- Revogação individual testada, incluindo encerramento das conexões existentes
  e negação de novo login, sem precisar trocar credenciais de outros aparelhos.
- Retenção local, exportações e notificações de tela bloqueada revisadas no
  piloto. Senha IRC não concede SSH, permissão de execução ou acesso global aos
  produtos RBX.

Começar pelo POCO; incluir Samsung com conta e salas próprias quando seu papel
estiver definido. Esta revisão registra a decisão para preparar o piloto; não
cria contas, segredos, listeners ou permissões na infraestrutura em execução.
Procedimento e dependências:
[clientes](../../../docs/network/local/CLIENTS-AND-STRATEGOS.md) e
[roadmap local](../../../docs/network/local/ROADMAP.md).

### Integração futura dos produtos RBX

Um gateway/BFF RBX deve validar `issuer`, `audience`, assinatura, expiração e o par estável
`(issuer, subject)`, resolver roles/membership do tenant e então criar uma
sessão IRC curta e revogável ou atuar como gateway IRC controlado.
O painel Strategos mantém a credencial do conector no servidor. A exceção
operacional dos clientes IRC dedicados não altera essa fronteira.

Ergo 2.19 oferece autenticação OAuth2 por introspecção e JWT, além dos
mecanismos SASL bearer. Isso torna uma integração com ZITADEL tecnicamente
possível, mas ela exige um ensaio específico de claims, audience, revogação e
rotação de chaves antes de ser habilitada:

- <https://github.com/ergochat/ergo/blob/v2.19.1/default.yaml>
- <https://github.com/ergochat/ergo/blob/v2.19.1/docs/MANUAL.md>

Não use access token Google diretamente como senha IRC e não derive identidade
de e-mail ou nickname. Google é o provedor de login; ZITADEL é o emissor e a
fonte de grants; o identificador canônico é o subject emitido pelo ZITADEL.

## Tenants e modo Holding

O modo Holding agrega visão, mas não amplia autorização. O desenho futuro deve
manter um mapa imutável `canal -> tenant` e exigir o contexto
`(account, channel, tenant)` em toda consulta. Uma conta pode visualizar vários
tenants somente quando os grants do ZITADEL autorizarem cada tenant; respostas
do bot nunca inferem tenant pelo texto digitado.

O adaptador já carrega `strategos.tenant_id` e passa conta, canal e tenant ao
contrato `StrategosClient`. O valor atual é `rbx`; isso prepara a fronteira sem
alegar suporte multi-tenant antes de ele existir no backend.

Salas recomendadas:

- `#strategos`: interface geral da holding, apenas dados explicitamente globais;
- `#tenant-<slug>`: conversa e leitura limitadas a um tenant;
- `#mission-<id>`: sala efêmera vinculada a uma missão canônica;
- `#rbx-founder`: decisões restritas ao fundador, sem execução direta.

## OpenClaw

Uma futura integração usa conta própria, sem `OPER` e sem compartilhar a conta
do Strategos. Ela deve permanecer limitada por configuração a tenants/canais,
aceitar somente mensagens explicitamente endereçadas, tratar conteúdo IRC como
entrada não confiável e manter DMs e replay desligados por padrão.

Antes de qualquer modelo ou ferramenta receber uma mensagem, são obrigatórios:

1. rate limit por conta e canal;
2. limite de tamanho, timeout e circuit breaker;
3. proteção contra prompt injection e ausência de transcrição ambiente;
4. autorização RBAC/policy para cada ação;
5. evidência no ledger para toda ação mutável;
6. nenhuma execução arbitrária de shell.

## Gates para habilitar a API real

O `strategos-core` atual não possui middleware de autenticação/tenant e não está
implantado como API na Corbetti. A chave `MAESTRO_DASHBOARD_KEY` também não é
adequada: além das leituras, ela autoriza admissão de missão e criação de lease.
Não entregue essa chave ao bot. O Maestro precisa de uma credencial de serviço
com audience e rotas estritamente read-only antes da conexão.

1. Implantar um endpoint Strategos autenticado por ZITADEL.
2. Tornar tenant obrigatório no contrato da API e na persistência do servidor.
3. Mapear canais e contas a tenants por configuração revisada.
4. Implementar timeouts, respostas limitadas e tratamento de indisponibilidade.
5. Testar isolamento cruzado entre tenants e negação de replay/DM.
6. Manter comandos mutáveis desabilitados até policy engine e ledger completos.
