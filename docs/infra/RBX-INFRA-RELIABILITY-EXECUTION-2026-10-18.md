# RBX Infra: execução da primeira etapa de confiabilidade

18 de outubro de 2026. Horários abaixo em America/Sao_Paulo; o JSON de
evidências usa UTC. Execução autorizada após o
[plano de continuidade](../PLAN-rbx-infra-reliability-2026-10-18.md).

## Resultado verificado

Cinco nós Ready. Os seis Applications conferidos — sites RBX, Robson,
ArgoCD, kube-prometheus-stack e Loki — estão Synced e Healthy. Traefik e
CoreDNS têm duas instâncias prontas em hosts distintos. O frontend do Robson
abriu a tela de login e o botão Google nos dois domínios de produção; sete
arquivos JavaScript responderam 200 com o tipo correto. O fluxo autenticado e
operações financeiras não fizeram parte desse smoke test.

A primeira etapa foi executada. A meta de continuidade completa e capacidade
N-1 continua pendente das decisões e provas listadas ao final.

## Mudanças aplicadas

| Frente | Estado aplicado |
|---|---|
| Ingresso e DNS | Duas réplicas, anti-afinidade obrigatória por host, pedidos de recursos, probes e PDB mantendo uma instância nas interrupções voluntárias. |
| Persistência no k3s | HelmChartConfig do Traefik e manifests próprios de CoreDNS idênticos nos três servidores. `.skip` impede a reconciliação concorrente do Deployment CoreDNS empacotado; Service, RBAC e ConfigMap foram preservados. |
| Agendamento crítico | PriorityClass `rbx-infra-critical`, com `preemptionPolicy: Never`. Ingresso e DNS priorizam a fila sem expulsar aplicações. |
| Monitoramento | Alertmanager, Grafana, Prometheus, Loki e cache recriados com toleration persistente da manutenção. As cinco exceções manuais de `unschedulable` deixaram de ser necessárias. |
| Memória do monitoramento | Prometheus solicita 1536 MiB e limita 2 GiB; Grafana solicita 256 MiB, além dos sidecars; cache Loki armazena 256 MiB, solicita 320 MiB e limita 384 MiB. Os quatro PVCs originais continuam Bound, com 116 dias de idade. |
| ArgoCD | Controlador sem PVC colocado no sumatrae: pedido de 1 GiB, limite de 2 GiB, `GOMEMLIMIT=1600MiB`. Concorrência de status/operações/kubectl reduzida a 10/2/5. Componentes e hook podem usar a capacidade de manutenção. |
| Robson | Instância única do robsond recuperada no sumatrae com toleration persistente e estratégia Recreate. Imagem `sha-145e1c1c` e configuração de negociação mantidas. |
| Reserva do sistema | Apenas sumatrae: 750m CPU e 2,5 GiB reservados entre Kubernetes e sistema, mais limiar de evacuação de 500 MiB. Allocatable confirmado: 3250m CPU e 4.881,18 MiB. |
| Alertas | Regras rápidas para indisponibilidade/redundância de ingresso e DNS, nó não Ready e memória disponível baixa. Regras e canal anteriores preservados. |
| CI | Dez testes do guard; rejeição de valores Helm ambíguos nos 53 Applications; renderização real dos três charts fixados com contratos de armazenamento, imagem e rastreamento. |

O sumatrae mantém `rbx.io/maintenance=stabilization:NoSchedule`, com responsável
RBX Infra e revisão registrada para 19/10. O cordon genérico foi retirado. O
bengal conserva a taint de piloto; somente um canário stateless do frontend foi
validado ali. O pod temporário estava ausente na conferência de encerramento.
Um Deployment legado de notificações ArgoCD, já desabilitado na fonte e fora
da reconciliação atual, foi escalado a zero.

## Incidentes e correções durante a execução

O tiger voltou a perder os sinais do kubelet às 14h45m42s. A memória disponível
chegou perto de 600 MiB; o controlador ArgoCD sem limite cresceu para cerca de
2 GiB, além do k3s e das aplicações. A API pública do Robson passou a responder
503, embora o processo do backend ainda respondesse 200 diretamente. Os sites
e o frontend permaneceram em 200 nas sondagens registradas.

Após confirmar os outros dois servidores e seus etcd saudáveis, o processo
antigo do controlador ArgoCD foi encerrado e apenas o k3s do tiger foi
reiniciado. A recuperação liberou cerca de 4 GiB de memória. A instância do
robsond que o Kubernetes tentou recriar ficou Pending por falta de capacidade
nos hosts gerais. O processo anterior já não estava executando quando a
instância única foi admitida no sumatrae. A primeira resposta 200 de recuperação
foi confirmada às 14h57m31s. O comportamento observado sustenta a investigação
de pressão de recursos; não comprova sozinho um OOMKill nem encerra a causa
original do incidente do sumatrae.

O primeiro rollout de componentes com prioridade de sistema também preemptou
três pods, de Flightdeck, Portal e Eden System Creator. Seus Deployments foram
posteriormente conferidos com duas réplicas disponíveis. A prioridade foi
trocada pela classe sem preempção. Nenhum novo evento de preempção foi
encontrado após essa mudança na conferência de encerramento.

A combinação inicial de `helm.valuesObject` com `helm.values` foi um erro de
implementação: ArgoCD ignora o segundo campo. A renderização local com dois
arquivos `-f` mascarou essa diferença. A validação ao vivo do Loki revelou a
configuração incompleta; a configuração padrão também chegou a alterar a
imagem declarada do controlador ArgoCD para v3.4.6. O controlador GitOps foi
temporariamente parado, as operações em curso foram encerradas e os valores
completos restaurados. A imagem gerenciada voltou a v3.4.7 antes da retomada.
O CI agora rejeita o caso original e verifica volumes, imagem, modo do Loki e
identidade com a precedência efetiva do ArgoCD.
[Precedência oficial](https://argo-cd.readthedocs.io/en/stable/user-guide/helm/#helm-value-precedence).

Na adoção do Grafana, o campo antigo `rollingUpdate` impedia a troca para
Recreate. Esse campo foi removido em uma atualização restrita da estratégia,
e a reconciliação terminou saudável. O procedimento está no runbook.

## Provas de recuperação

| Verificação | Resultado |
|---|---|
| Eviction de um Traefik, respeitando PDB | Duas instâncias prontas recompostas em 9,3 s. |
| Eviction de um CoreDNS, respeitando PDB | Duas instâncias prontas recompostas em 7,6 s. |
| DNS direto nas duas instâncias, antes e depois | Oito consultas válidas, incluindo UDP e TCP. |
| HTTP durante as evictions, às 15h24 | 16 respostas 200, nenhuma falha registrada. |
| HTTP na janela da reserva/reinício do sumatrae | 72 respostas 200, nenhuma falha registrada. |
| Volumes do monitoramento | Quatro PVCs originais Bound: 2, 5, 10 e 20 GiB. |
| Estado final | Quatro pods críticos prontos em altaica/tiger, cinco componentes de monitoramento prontos, uma instância pronta do robsond e cinco nós Ready. |

As duas séries públicas somam 2.640 verificações em quatro URLs. A primeira
registrou 57 respostas 503 da API durante a nova falha do tiger. A segunda
registrou oito respostas 503 durante a recriação controlada que tornou
persistente o agendamento do backend, entre 15h00m24s e 15h00m53s; a próxima
resposta foi 200 às 15h00m58s. Não houve falhas dos dois sites ou do frontend
nessas séries. As janelas e os intervalos de amostragem estão na evidência;
esses resultados não constituem um SLO mensal nem provam failover do IP público.

## Capacidade e pendências para continuar

O guard do sumatrae conferiu 4.614 MiB de pedidos antes da reserva. Após os
testes, foram medidos 4.416 MiB e 1090m CPU, com aproximadamente 3.033 MiB de
memória disponível e pressão de memória zero. Os pedidos ainda ocupam cerca
de 90,5% do Allocatable de memória desse nó. Isso comprova encaixe da etapa,
com a barreira de manutenção, e não folga N-1. Tiger e altaica continuam sem a
nova reserva até redistribuição e capacidade adicionais.

1. **Entrada externa:** os cinco nomes públicos conferidos continuam resolvendo
   para o IPv4 do tiger. Falta escolher e provisionar balanceamento externo com
   health checks, alterar DNS e testar a perda real desse host.
2. **Capacidade:** definir orçamento e workers adequados, completar a homologação
   dos destinos e distribuir as cargas antes de ampliar as reservas. Jaguar
   conserva sua dedicação a analytics/banco; bengal conserva o estado de piloto.
3. **Detecção independente:** implantar o monitor fora do cluster e comprovar o
   canal de entrega. O teste identificado de e-mail aguarda autorização; não
   foi tratado como entregue. As sondagens locais desta sessão foram encerradas
   após os testes e não são o monitor externo permanente.
4. **Backup PostgreSQL:** jaguar tem 1.645 MB de bancos e cerca de 330 GB livres,
   mas `archive_mode=off` e pgBackRest ausente. A referência de chave prevista
   não foi encontrada no store consultado. Definir custódia e janela de reinício
   do banco compartilhado; depois instalar, tirar o primeiro backup e provar
   restore. O destino externo e a custódia de recuperação também precisam ser
   efetivados. Existência de snapshots etcd não foi usada como prova de restore.
5. **Aceite posterior:** observar os novos perfis por sete dias, verificar picos,
   concluir backups/estado e executar os exercícios N-1 do plano. As demais
   divergências de Applications fora deste escopo conservam seu próprio backlog.

## Fontes da execução

- [Evidência estruturada](evidence/rbx-reliability-20261018.json): estado final,
  probes agregados e hashes dos logs originais, evictions, DNS e smoke do FE.
- [Runbook de operação e reversão](../runbooks/RBX-INFRA-RELIABILITY.md).
- PRs de implementação: [372](https://github.com/rbxrobotica/rbx-infra/pull/372),
  [373](https://github.com/rbxrobotica/rbx-infra/pull/373),
  [374](https://github.com/rbxrobotica/rbx-infra/pull/374) e
  [375](https://github.com/rbxrobotica/rbx-infra/pull/375).
