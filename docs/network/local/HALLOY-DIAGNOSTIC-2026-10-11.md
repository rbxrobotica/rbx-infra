# Halloy: atualização visual, memória e persistência

Data: **2026-10-11, America/Sao_Paulo**. Estado: diagnóstico inicial concluído;
**sintoma ainda não reproduzido e causa não confirmada**. O operador relata
que, após muito tempo aberto, Halloy deixa de mostrar atualizações e volta a
mostrá-las ao fechar e reabrir a janela. Konversation foi
[removido a pedido do operador](KONVERSATION-PILOT.md). O tema escuro Halloy
foi aprovado e permanece inalterado.

**2026-10-12 update:** The observations below remain the October 11 baseline.
The symptom was subsequently captured as a black main window with sustained
CPU use near one core and stable resident memory. The
[October 12 incident record](HALLOY-INCIDENT-2026-10-12.md) contains the live
measurements and their limits; the exact cause remains unknown. After the
operator authorized a restart with software rendering, the interface recovered
and short samples showed much lower CPU consumption. Long-term stability remains
unverified; see the incident record for the experiment and reversal procedure.

## Ambiente e evidência

- Halloy **2026.9 (2b3edc8e)**, binário existente em
  `~/.local/opt/halloy/2026.9/bin/halloy`.
- Linux Mint 22.3, **XFCE/X11**, RTX 4070 Ti SUPER, driver NVIDIA 595.99.02.
  Bibliotecas Vulkan/NVIDIA aparecem no processo; isso não prova defeito gráfico.
- Tema `ferra`; previews desabilitados; RBXNet pelo túnel
  `127.0.0.1:6667`; `automated_chathistory=true`; SASL referencia o cofre.
  A senha não foi recuperada nem migrada.
- SHA256 da configuração preservada:
  `b66b92cc4231b9d54fd0f3f2e8e7fb6be2701c8c0d7cd548bd9f8eaa75940921`.

O processo observado iniciou às 14:30:59 BRT. Sete amostras a cada 30 segundos,
entre **14:41:59 e 14:45:00 BRT**, apresentaram:

| Medida | Resultado |
| --- | --- |
| RAM residente, RSS | 299.368 KiB, cerca de 292,4 MiB; constante |
| PSS, que rateia páginas compartilhadas | 204.055 a 204.056 KiB, cerca de 199,3 MiB |
| Pico RSS observado pelo processo | 299.368 KiB; constante |
| Swap do processo | 0 KiB |
| CPU, percentual de um núcleo por intervalo | 1,97% a 2,23% |
| Threads / descritores abertos | 70 / 87; constantes |

A [série de medições](evidence/halloy-memory-2026-10-11.json) contém somente
métricas. Não houve crescimento relevante nesses três minutos. É uma amostra
curta, logo após a abertura, sem reprodução do sintoma: **não exclui vazamento
após horas ou dias**, retenção de objetos nem falha de renderização.

### Persistência em disco

A leitura e descompressão de **23 arquivos JSON/gzip** em
`~/.local/share/halloy` terminou sem erro: 19 arquivos de histórico, dashboard,
rascunhos, janela e um arquivo de targets. Os históricos somavam 30.472 bytes
em disco; o conjunto validado tinha 337.506 bytes após descompressão. Não foram
apagados, alterados ou copiados para o repositório.

Isso confirma legibilidade sintática, não a presença de todas as mensagens ou
a correção lógica dos cursores. Os quatro logs existentes não apresentaram
linhas WARN/ERROR nem os indicadores pesquisados de timeout, panic, OOM ou
falha de leitura/gravação. Logs sem erro não demonstram ausência de bug.

O [código de histórico da versão 2026.9](https://github.com/squidowl/halloy/blob/2026.9/data/src/history.rs#L34)
limita a 10.000 mensagens cada histórico persistido e grava em JSON comprimido.
Esse limite não representa um teto de RAM do processo. Já
[`automated_chathistory`](https://halloy.chat/configuration/servers#automated-chathistory)
trata da recuperação de histórico do servidor, não da coleta de lixo da interface.

### Transporte

`rbx-irc-tunnel.service` e `rbx-agent-irc.service` estavam ativos, com
`NRestarts=0` desde a ativação atual em 8 de outubro. O socket do Halloy e o
socket separado da mailbox estavam estabelecidos; as filas do Halloy estavam
zeradas. O túnel já configura `ServerAliveInterval=30`,
`ServerAliveCountMax=3` e `ExitOnForwardFailure=yes`.

Esses dados não apontam falha atual do túnel. TCP estabelecido e filas vazias
não comprovam entrega IRC nem atualização do painel. Não houve envio de
mensagem de teste, captura de payload ou reinício dos serviços.

## Hipóteses apoiadas por relatos upstream

| Relato oficial | Relação com este ambiente | O que falta observar |
| --- | --- | --- |
| [#2437: rolagem presa](https://github.com/squidowl/halloy/issues/2437), aberto em 9 de outubro | Halloy 2026.9, Linux/X11; rolagem de histórico não lido pode prender e saturar um núcleo | Confirmar se o sintoma envolve rolagem, CPU alta e recuperação com PgUp ou arraste da barra |
| [#2129: painel deixa de redesenhar](https://github.com/squidowl/halloy/issues/2129) | Relatos de CPU alta e RAM estável; há comentários em Linux/NVIDIA | Verificar se título/canal mudam enquanto a conversa permanece congelada |
| [#2228: memória cresce com o tempo](https://github.com/squidowl/halloy/issues/2228) | Relato em 2026.8 após uso por dias | Medir tendência de RSS/PSS no mesmo processo durante uso prolongado e no momento da falha |

**Correction verified on 2026-10-12:** None of these reports establishes the
local cause. [Halloy PR #2354](https://github.com/squidowl/halloy/pull/2354)
introduced an Iced NVIDIA/Vulkan recovery change, but inclusion of that PR does
not mean the recovery remains active in Halloy 2026.9. The
[release's dependency pin](https://github.com/squidowl/halloy/blob/2026.9/Cargo.toml#L169-L171)
selects Iced `3124ac8a68529d10a34f708431ffc373615f9470`, which contains
[revert `a4abb0f`](https://github.com/squidowl/iced/commit/a4abb0fe890767dc32604a13cc0a49c2f32819ed).
[Iced PR #3478](https://github.com/iced-rs/iced/pull/3478) records the original
presentation-error loop and a subsequent Halloy/Vulkan redraw regression that
preceded the revert. The
[pinned code](https://github.com/squidowl/iced/blob/3124ac8a68529d10a34f708431ffc373615f9470/winit/src/lib.rs#L953-L995)
again requests redraw after generic presentation errors. This is a plausible
failure mechanism, not a diagnosis: the October 12 capture did not contain the
specific surface-error log signature. Reinstalling the same 2026.9 release is
not a new fix.

## Captura no próximo sintoma

O [script de captura](../../../infra/irc/scripts/halloy-health-snapshot.py)
usa somente Python 3 padrão e `/proc`. Não instala daemon, não sinaliza
processos, não abre conexões, não lê conversas e não altera configuração.
Lê os bytes da configuração apenas para SHA256; não imprime seus valores.
Da persistência, registra apenas contagem, tamanho e data da última alteração.

Na raiz deste checkout de `rbx-infra`, executar durante uso normal e novamente
quando o sintoma aparecer, **antes de fechar Halloy**:

```bash
(
  umask 077
  python3 infra/irc/scripts/halloy-health-snapshot.py --interval 2 \
    > "$HOME/halloy-snapshot-$(date +%Y%m%d-%H%M%S).json"
)
```

O resultado fica fora do Git, com acesso restrito ao usuário. Verificar os
campos `errors` e `processes`: ausência do processo ou erro de leitura não
equivale a estado saudável. A captura cobre apenas o processo Halloy e seus
sockets TCP IPv4 para loopback:6667; não mede RAM de GPU, processos auxiliares
nem processamento do servidor. Não é um monitor automático.

Junto à captura, registrar horário e comportamento: troca de canais, digitação,
rolagem, janela minimizada, suspensão ou troca de rede. Uma sala silenciosa
não demonstra falha; confirmar se houve mensagens novas por evidência disponível
sem publicar testes não autorizados.

Se a rolagem estiver presa, anotar se PgUp ou arrastar a barra recupera a
interface **depois da captura**. Se houver CPU alta sem crescimento de RAM,
investigar loop visual; se RSS/PSS crescerem persistentemente, investigar
retenção de memória; se houver desconexão ou fila de recepção persistente,
investigar transporte e processamento. Cada sinal orienta investigação, não
fecha diagnóstico sozinho.

Só realizar teste controlado de backend gráfico ou versão após essa distinção,
com uma variável por vez e possibilidade de retorno. Não apagar histórico,
ativar log bruto de IRC ou trocar o tema como primeira medida. O tema `ferra`
e a integração com o cofre continuam aprovados. A consolidação no bootstrap
Mint será posterior à validação de uso prolongado e reconexão.

## Verificação da ferramenta

Compilação Python e execução real concluídas; revisão independente conferiu
índices de CPU, unidades, correlação de sockets por inode e erros de processo
encerrado, com casos sintéticos em memória. A captura real não imprimiu valores
de configuração nem conteúdo de conversa. Trata-se de instrumento de coleta,
não de uma correção aplicada ao Halloy.
