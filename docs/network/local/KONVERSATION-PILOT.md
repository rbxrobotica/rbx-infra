# Revisão do piloto Konversation

Data: 2026-10-11. Estado: instalado, mas não adotado como padrão RBX.
O operador prioriza leveza, baixo consumo, soberania, artefatos verificáveis e
reconstrução pela automação. A instalação Flatpak resolveu a disponibilidade
do aplicativo, mas não demonstrou aderência a esses critérios.

## Resultado da revisão

O screenshot fornecido mostra menus escuros e área de conversa branca. A paleta
Qt `BreezeDark` não configura as cores próprias do histórico do Konversation.
O aplicativo também abriu a rede pública Libera, enquanto o Halloy já contém
RBXNet, conta SASL e sete canais. Portanto, instalação e paleta não entregaram
equivalência operacional.

Recomendação do coordenador: preservar Halloy como cliente principal, com
diagnóstico da atualização interrompida. Konversation permanece piloto opcional.
Isso não afirma que o Halloy é mais estável, nem que Konversation é inseguro.
O defeito relatado e os critérios de credencial/empacotamento ainda impedem a
escolha definitiva. Nenhum aplicativo foi removido nesta revisão.

## Autenticação e perfil RBX

A inspeção por campos confirmou que Halloy usa `sasl.plain.password_keyring`.
O segredo não foi recuperado nem copiado. Dados não secretos do perfil:

- Rede: `rbx`, apresentação proposta `RBXNet`.
- Servidor: `127.0.0.1:6667`, sem TLS apenas no trecho loopback do túnel SSH.
- Apelido configurado: `leandro-mint`; conta SASL: `leandro`.
- Salas: as sete já registradas no inventário IRC.

O código oficial do Konversation v26.04.3 lê e grava a senha SASL em texto no
`konversationrc`. A integração KWallet encontrada atende senha de proxy, não
esse campo. Sem senha, o cliente pula SASL; não oferece prompt de senha SASL
por conexão. Fontes: [serialização da identidade](https://invent.kde.org/network/konversation/-/blob/v26.04.3/src/application.cpp#L401),
[início SASL](https://invent.kde.org/network/konversation/-/blob/v26.04.3/src/irc/server.cpp#L745).

Modo `0600` restringe leitura a outros usuários, mas não criptografa o arquivo
nem protege de processos com o mesmo usuário. O cofre também não constitui
isolamento absoluto contra comprometimento de uma sessão desbloqueada. Ainda
assim, copiar a senha do cofre para texto amplia as cópias legíveis e deve ser
uma decisão explícita, não um efeito oculto de trocar o cliente.

O [perfil preparado](konversation-pilot.prepared.json) contém somente campos
não secretos, autoconexão desabilitada e autenticação pendente. É um plano de
configuração, não um arquivo que o Konversation consome. Preserva identidades
separadas para redes públicas. Nenhuma senha foi migrada nem login realizado
por esta revisão.

## Correção completa de cores

O grupo `Message Text Colors` controla onze cores de conversa, incluindo
`TextViewBackground`, `ChannelMessage`, `BacklogMessage` e `Hyperlink`.
`Flags/InputFieldsBackgroundColor=true` estende essa paleta à entrada e listas.
As cores de apelidos e códigos IRC precisam de contraste próprio; manter
`BreezeDark` para os demais controles Qt. Fonte:
[schema da versão instalada](https://invent.kde.org/network/konversation/-/blob/v26.04.3/src/config/konversation.kcfg#L917).

Para aplicar o perfil, encerrar primeiro o aplicativo para que salve as redes
existentes. Fazer cópia privada do arquivo, mesclar usando grupos numerados
livres e conferir referências de identidade, rede e canais. Alterar o arquivo
com o programa aberto pode perder mudanças no encerramento. Não substituir
as redes existentes nem iniciar automaticamente uma conexão com credencial
incompleta. A confirmação visual deve ocorrer após nova abertura.

## Distribuição e consumo

| Opção observada | Evidência local | Limite |
| --- | --- | --- |
| Flatpak atual | Konversation 1.10.26043, runtime KDE 6.10 | Runtime reporta cerca de 1,1 GB em disco; isso não representa RAM residente |
| APT da distribuição | Candidato 23.08.5-0ubuntu4, Noble/universe | Simulação sem recomendações acrescenta 95 pacotes nesta máquina, principalmente Qt5/KF5 |
| Binário Linux avulso oficial | Não encontrado nas páginas oficiais consultadas | Não prometer um equivalente ao artefato Halloy; compilar exige manter dependências e builds |

Fontes: [KDE](https://apps.kde.org/konversation/),
[downloads oficiais](https://konversation.kde.org/download.html),
[arquitetura Flatpak](https://docs.flatpak.org/en/latest/under-the-hood.html).
Nenhuma migração de pacote foi executada. A preferência do operador por evitar
Flatpak passa a orientar a escolha; não tratar o piloto como padrão de bootstrap.

SHA-256 verifica bytes contra um valor esperado. Autenticidade depende também
da procedência desse valor ou de assinatura verificável. APT usa metadados
assinados e hashes de pacotes; isso é diferente de assinar cada `.deb`.
Fonte: [apt-secure do Ubuntu Noble](https://manpages.ubuntu.com/manpages/noble/man8/apt-secure.8.html).

Comparar CPU/RAM com a mesma rede, salas e volume de histórico, depois de
estabilizar a carga, incluindo inatividade e retomada. Registrar memória PSS,
CPU por intervalo, processos auxiliares e uso em disco separadamente. Os
screenshots mostram cargas diferentes: Libera com um canal e RBXNet com sete.
Não concluir superioridade por uma leitura isolada dos processos.

## Consolidação posterior no bootstrap

O catálogo atribui o Mint local a `ldamasio/rbx-workstation-mint`, disponível em
`/home/psyctl/app/rbx-workstation-mint`. O repositório `rbx-workstation` continua
centrado na VPS e não substitui esse proprietário. O checkout Mint contém uma
alteração do operador em `group_vars/all/main.yml`, preservada nesta revisão.

A implementação futura deve declarar cliente/distribuição, versão e origem,
verificação de artefato, tema completo, perfil RBX e referência ao cofre.
Preservar contas, outras redes e o cliente anterior até aceitar o piloto.
Validar idempotência, recuperação da configuração e atualização deliberada;
não registrar senhas em templates, variáveis, diffs ou logs. A role ainda não
foi implementada, conforme a indicação do operador de consolidá-la depois.

Registro documental: RBX Voice System v0.1, R4 com sobreposição R6.
