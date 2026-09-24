# Segurança do IRC RBX

## Fronteiras obrigatórias

- Bots internos operam somente no Ergo RBX. Bots de IA não entram em Libera,
  OFTC, hackint ou tilde.chat sem autorização explícita da rede e do canal.
- Canais de terceiros não são enviados a LLMs nem registrados por bots RBX.
- Senhas, tokens, chaves, cookies, material de certificado e dados de cliente
  nunca devem ser publicados em canal.
- Nenhum comando IRC pode executar shell arbitrário, SQL, `kubectl`, deploy ou
  alteração de infraestrutura.
- Strategos, Maestro, Git e o ledger são canônicos; mensagens IRC não são estado
  de missão nem evidência suficiente de aprovação.

## Transporte e exposição

ZNC deve permanecer em `127.0.0.1:6501`. O Ergo plaintext deve permanecer em
`127.0.0.1:6667`; ele existe apenas para túneis locais. Fora do túnel, TLS com
verificação de certificado é obrigatório em `6697`. A porta TLS pode ser ligada
a um IP Tailscale/VPN revisado, nunca a `0.0.0.0` ou `::`.

Não publique WebAdmin, websocket, proxy reverso, DNS público ou regras de
firewall nesta fase. Revise também o firewall de nuvem antes de qualquer mudança
de bind: o firewall do sistema operacional sozinho não define a exposição.

## Identidade e autorização

- Ergo exige SASL e desabilita autorregistro.
- Contas iniciais são criadas por operador e recebem senha temporária rotacionada
  no primeiro acesso.
- Canais são secretos/invite-only e ACLs usam contas, não apenas nicknames.
- O bot autoriza pela tag IRCv3 `account`, nunca por nick/hostmask.
- A lista de contas autorizadas é explícita e o padrão é negar.
- Credenciais de bot devem ser exclusivas e sem privilégios de operador.

Comandos destrutivos futuros precisam passar por RBAC, policy engine, confirmação
fora de banda quando aplicável e gravação de decisão/resultado no ledger. O stub
`!mission approve` não concede aprovação.

## Segredos e arquivos

Arquivos `.env`, `ircd.yaml`, chaves TLS, configuração ZNC e configuração real do
bot ficam fora do Git. Use modo `0600` para arquivos sensíveis e `0700` para os
diretórios. Não coloque segredo na linha de comando quando houver alternativa por
arquivo ou variável de ambiente; limpe variáveis de sessões de diagnóstico.

Rotacione imediatamente qualquer credencial exposta e, no mínimo, a cada 90 dias
para contas de automação. Revogue contas de pessoas no desligamento de acesso.

## Logs, histórico e privacidade

O Ergo exclui `userinput`, `useroutput` e `connect-ip` dos logs. O driver do
container retém no máximo três arquivos de 10 MiB. O histórico fica em memória,
expira em sete dias e só pode ser consultado desde a entrada no canal. Para um
canal sensível, o proprietário deve desativar o histórico com o comando suportado
pela versão do Ergo ou usar mensagens efêmeras fora do IRC.

Nunca registre payloads, senhas SASL, mensagens privadas ou conteúdo integral de
comandos no bot. Revise backups como dados sensíveis e aplique a mesma política de
retenção do serviço.

## Backup e resposta

Backups param brevemente o container para obter uma cópia coerente, são criados
com modo `0600` em `/srv/rbx/irc/backups` e não saem da Corbetti por padrão.
Teste restauração trimestralmente. Em incidente:

1. remova o acesso da conta e preserve os logs sem ampliar sua distribuição;
2. pare o bot afetado;
3. rotacione credenciais e certificados relevantes;
4. registre o incidente e as ações no sistema canônico;
5. restaure serviço somente após validar binds, ACLs e healthcheck.
