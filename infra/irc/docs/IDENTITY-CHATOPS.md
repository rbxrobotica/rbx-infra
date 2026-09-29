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
expira em sete dias e a persistência está desativada.

## Android, Google e ZITADEL

O app Strategos deve ser um cliente nativo público, sem client secret. O login
abre o navegador do sistema, autentica Google como provedor federado do ZITADEL
e usa Authorization Code com PKCE. A documentação oficial do ZITADEL recomenda
PKCE para aplicações nativas:

- <https://zitadel.com/docs/guides/integrate/login/oidc/oauth-recommended-flows>
- <https://zitadel.com/docs/guides/integrate/login/oidc/login-users>

O Poco não deve armazenar senha SASL persistente. Um gateway/BFF RBX deve
validar `issuer`, `audience`, assinatura, expiração e o par estável
`(issuer, subject)`, resolver roles/membership do tenant e então criar uma
sessão IRC curta e revogável ou atuar como gateway IRC controlado.

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
