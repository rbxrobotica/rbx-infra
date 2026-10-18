# RBX Infra: redundância e capacidade

Escopo: cluster identificado pelo UID de `kube-system`, com k3s v1.36.2+k3s1,
Traefik 40.1.3+up40.1.0 e CoreDNS 1.14.4. Não executar `site.yml` para esta mudança.

## Aplicação dos componentes críticos

Usar o inventário e as credenciais existentes na estação do operador. Definir
`rbx_cluster_kube_system_uid` com o UID conferido, fora de qualquer credencial.

```sh
ansible-playbook -i inventory/hosts.yml reliability.yml --tags critical-services --check
ansible-playbook -i inventory/hosts.yml reliability.yml --tags critical-services
```

O primeiro play coloca `coredns.yaml.skip` em **todos** os servidores antes de
instalar a configuração idêntica. Isso impede que o componente empacotado volte
a uma réplica, sem apagar DNS, Service, RBAC ou ConfigMap. Os arquivos próprios
`rbx-coredns.yaml`, `rbx-coredns-pdb.yaml` e `rbx-traefik-config.yaml` ficam no
diretório de manifests do k3s em cada servidor. Não usar `--disable=coredns`:
essa opção desinstala o componente. Não editar os manifests gerados pelo k3s.

O Deployment CoreDNS é explicitamente mantido aqui; o ConfigMap com Corefile e
NodeHosts continua existente. Antes de atualizar k3s, comparar o novo Deployment
empacotado com esta cópia e atualizar imagem, volumes, portas e probes no mesmo
processo. A presença do `.skip` é uma obrigação de manutenção, não uma migração
automática para versões futuras.

O Traefik usa HelmChartConfig com duas réplicas. Certificados continuam sendo
gerenciados pelo cert-manager; não habilitar armazenamento ACME local em cada
réplica. A anti-afinidade obrigatória impede as duas réplicas no mesmo host.
`maxSurge: 0` permite atualizar em apenas dois hosts elegíveis. O PDB preserva
uma réplica em interrupções voluntárias, mas não cobre perda abrupta de host.
A PriorityClass `rbx-infra-critical` mantém prioridade na fila com
`preemptionPolicy: Never`: pedidos de ingresso e DNS não devem expulsar outras
aplicações para fabricar capacidade. Uma réplica Pending exige corrigir a
capacidade e o agendamento; não remover essa proteção para forçar o rollout.

Esses manifests estão fora de `platform/`: o Application `platform` percorre
esse diretório recursivamente. Ter k3s e ArgoCD como donos dos mesmos recursos
criaria reconciliação concorrente.

## Saída do cordon e monitoramento

No sumatrae, a barreira é `rbx.io/maintenance=stabilization:NoSchedule`. Instalar
essa taint antes de retirar o cordon. Somente DNS, ingresso e os cinco
componentes de monitoramento, o controlador ArgoCD e a instância única do
robsond autorizados recebem a toleration correspondente.
Não adicionar toleration genérica de `unschedulable` aos controladores.

Os valores dos charts ficam nos dois Applications de monitoramento, em
`valuesObject`, que complementa os valores existentes. Os PVCs de Prometheus,
Grafana, Alertmanager e Loki permanecem vinculados ao sumatrae. Grafana usa
Recreate por causa do banco SQLite no PVC. O cache de resultados Loki é
descartável e limitado a 256 MiB de dados; seu contêiner reserva 320 MiB.

Conferir os controladores e os pods recriados: toleration persistente, PVC
correto, readiness e ausência de OOM. Só então remover qualquer exceção manual.
Não fazer drain de sumatrae ou altaica sem um procedimento para seus dados.

O controlador ArgoCD, sem PVC, é colocado temporariamente no sumatrae com pedido
de 1 GiB, limite de 2 GiB e paralelismo reduzido. Isso retira seu processo de
aproximadamente 1 GiB do tiger. Essa colocação precisa ser revisada ao adicionar
workers; ela não fornece alta disponibilidade ao próprio ArgoCD. Se sumatrae
falhar, alterar a colocação explicitamente após verificar o destino.
O processo Go tem `GOMEMLIMIT=1600MiB` dentro do limite de contêiner de 2 GiB;
isso antecipa a coleta de memória, mas não substitui medir a carga e os picos.

O robsond mantém uma réplica e estratégia Recreate. Sua toleration é uma
exceção de capacidade para recuperação, não autorização para múltiplos
executores. Antes de remover um pod preso num nó inacessível, comprovar que o
processo anterior parou. Não usar remoção forçada para criar outro executor
enquanto o anterior puder continuar executando. Não alterar imagens, migrações,
configuração de negociação ou credenciais ao corrigir apenas agendamento.

## Reservas do sistema: canário

Somente `host_vars/sumatrae.yml` habilita a reserva. O guard exige os outros
servidores Ready, APIs `/readyz` respondendo, duas instâncias prontas de DNS e
ingresso em hosts distintos, pedidos dos pods dentro do novo Allocatable e
memória disponível sem pressão significativa. Falha no guard interrompe o play.

```sh
ansible-playbook -i inventory/hosts.yml reliability.yml --tags resource-reservations --check
ansible-playbook -i inventory/hosts.yml reliability.yml --tags resource-reservations
```

O drop-in `90-rbx-reliability.yaml` acrescenta argumentos, preservando os já
existentes. Reserva 500m CPU/2 GiB para Kubernetes, 250m/512 MiB para o sistema e
500 MiB de limiar de evacuação por memória. O restart é serial e limitado ao
host habilitado. Depois, conferir `/readyz`, condições do nó e `Allocatable`;
não estender aos demais servidores sem acomodar seus pedidos atuais.

Rollback da reserva: retirar apenas esse drop-in, confirmar os outros dois
servidores e uma instância crítica fora do host, reiniciar apenas esse k3s e
verificar recuperação. Não substituir a configuração inteira do k3s.

## Verificação e rollback dos serviços

```sh
kubectl -n kube-system get deploy traefik coredns
kubectl -n kube-system get pods -o wide
kubectl -n kube-system get pdb
kubectl -n monitoring get pods -o wide
kubectl get nodes
```

Exigir duas réplicas disponíveis **em hosts distintos**, endpoints prontos,
resolução DNS por ambas as instâncias e HTTP público. Substituir um único pod
por vez e esperar a recomposição antes do próximo teste. Registrar sondagens
durante a substituição. Não desligar tiger como teste enquanto DNS público
apontar somente para seu IP: redundância de pods não resolve essa dependência.

Rollback de CoreDNS: preparar o Deployment da versão k3s instalada, remover os
arquivos próprios de CoreDNS em todos os servidores e só então retirar os
`.skip` coordenadamente. Preservar Service, RBAC, ConfigMap e DNS funcional
durante a transição. Não apagar o AddOn com cascata. Para Traefik, publicar a
versão anterior do HelmChartConfig nos três servidores e acompanhar o rollout.
Para monitoramento, reverter somente os valores desta mudança; não podar PVCs.

## Pendências que impedem declarar continuidade completa

- Entrada externa com health checks: os domínios ainda dependem do IP tiger.
- Capacidade N-1: tiger e altaica não comportam a reserva proposta com as cargas
  atuais; novos workers ou migração seletiva ainda são necessários.
- Métricas de sete dias para ajustar requests e limites sem subdimensionamento.
- Monitor externo e teste de entrega/retorno do canal de alerta.
- pgBackRest instalado, primeiro backup e restore comprovado; o restart do
  PostgreSQL e a custódia da chave seguem o runbook específico.
- Destino de backup externo e teste de restauração com RPO/RTO medidos.

Referências: [plano](../PLAN-rbx-infra-reliability-2026-10-18.md),
[backup PostgreSQL](PGBACKREST-BACKUP.md),
[componentes k3s](https://docs.k3s.io/installation/packaged-components),
[HelmChartConfig](https://docs.k3s.io/add-ons/helm).
