<script>
  import { onMount } from 'svelte';
  import Icon from '../lib/Icon.svelte';
  import { STORAGE_KEY, PEOPLE, STATES, PURPOSES, seed, validateSnapshot, visibleRequests, createRequest, transition, replyPending, allowedTransitions, demonstrationPdf } from '../lib/workflow.js';

  let state = $state(seed());
  let actor = $state('ana');
  let view = $state('list');
  let selectedId = $state('');
  let filter = $state('todos');
  let search = $state('');
  let year = $state('2024');
  let purpose = $state('');
  let confirmed = $state(false);
  let note = $state('');
  let replyYear = $state('2023');
  let ready = $state(false);
  let storageWarning = $state('');
  let error = $state('');
  let notice = $state('');
  let resetConfirm = $state(false);
  let importInput = $state();
  let main = $state();

  let person = $derived(PEOPLE[actor]);
  let staff = $derived(actor === 'secretaria');
  let all = $derived(visibleRequests(state, actor));
  let filtered = $derived(all.filter(r => (filter === 'todos' || r.status === filter) && `${r.id} ${PEOPLE[r.owner].name} ${r.purpose}`.toLowerCase().includes(search.trim().toLowerCase())));
  let request = $derived(all.find(r => r.id === selectedId));
  let inProgress = $derived(all.filter(r => !['disponivel','cancelado'].includes(r.status)).length);
  let available = $derived(all.filter(r => r.status === 'disponivel').length);
  let pending = $derived(all.filter(r => r.status === 'pendente').length);
  const actions = { analise: 'Iniciar análise', pendente: 'Solicitar informação', disponivel: 'Disponibilizar exemplo', cancelado: 'Cancelar pedido' };
  const date = value => new Intl.DateTimeFormat('pt-BR',{day:'2-digit',month:'short',year:'numeric',timeZone:'America/Sao_Paulo'}).format(new Date(value));
  const time = value => new Intl.DateTimeFormat('pt-BR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit',timeZone:'America/Sao_Paulo'}).format(new Date(value));

  onMount(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) state = validateSnapshot(JSON.parse(saved));
    } catch { storageWarning = 'Não foi possível ler a cópia local. Os exemplos estão carregados; você pode exportar esta sessão.'; }
    ready = true;
  });
  function persist(next) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(next)); storageWarning = ''; }
    catch { storageWarning = 'Seu navegador não permitiu salvar. As alterações valem nesta sessão; exporte uma cópia antes de sair.'; }
    state = next;
  }
  function navigate(target) {
    view = target; error = ''; notice = ''; resetConfirm = false;
    if (typeof window !== 'undefined') { window.scrollTo({top:0,behavior:'instant'}); setTimeout(()=>main?.focus(),0); }
  }
  function switchProfile() { selectedId=''; filter='todos'; search=''; note=''; navigate('list'); }
  function openRequest(r) { selectedId=r.id; replyYear=r.year; note=''; navigate('detail'); }
  function submit(event) {
    event.preventDefault(); error='';
    if (!confirmed) { error='Confirme o uso de dados fictícios para continuar.'; return; }
    try {
      const result = createRequest(state,actor,{year,purpose});
      if (!result.duplicate) persist(result.state);
      selectedId=result.request.id; view='detail'; note='';
      notice=result.duplicate ? 'Já existe um pedido em andamento com estes dados. Abrimos o pedido original para evitar duplicidade.' : 'Solicitação registrada. Guarde o protocolo abaixo para acompanhar o atendimento.';
      confirmed=false;
    } catch (e) { error=e.message; }
  }
  function change(target) {
    error='';
    try { persist(transition(state,actor,selectedId,target,note)); note=''; notice='Situação atualizada. O evento está registrado no histórico e visível ao solicitante.'; }
    catch(e) { error=e.message; }
  }
  function reply(event) {
    event.preventDefault(); error='';
    try { persist(replyPending(state,actor,selectedId,replyYear)); notice='Informação confirmada e devolvida à secretaria para análise.'; }
    catch(e) { error=e.message; }
  }
  function download(content, type, filename) {
    const url=URL.createObjectURL(new Blob([content],{type}));
    const link=document.createElement('a'); link.href=url; link.download=filename; link.click();
    setTimeout(()=>URL.revokeObjectURL(url),1500);
  }
  function pdf() {
    try { download(demonstrationPdf(request,actor),'application/pdf',`${request.id}-DEMONSTRACAO.pdf`); }
    catch(e) { error=e.message; }
  }
  function exportDemo() { download(JSON.stringify(state,null,2),'application/json','secretaria-digital-demo.json'); notice='Cópia local exportada. Ela contém todos os perfis fictícios desta demonstração.'; }
  async function importDemo(event) {
    const file=event.target.files?.[0]; if(!file) return;
    error='';
    try {
      if(file.size>2_000_000) throw new Error('A cópia deve ter no máximo 2 MB.');
      const parsed=validateSnapshot(JSON.parse(await file.text()));
      persist(parsed); selectedId=''; view='list'; notice='Cópia restaurada. Os pedidos anteriores foram substituídos pela cópia importada.';
    } catch(e) { error=e instanceof SyntaxError ? 'O arquivo não contém uma cópia JSON válida.' : e.message; }
    event.target.value='';
  }
  function reset() { persist(seed()); selectedId=''; view='list'; resetConfirm=false; notice='Demonstração reiniciada com os quatro pedidos fictícios de exemplo.'; }
</script>

<svelte:head>
  <title>Secretaria Digital — protótipo do PI VI</title>
  <meta name="description" content="Protótipo acadêmico para solicitar e acompanhar históricos escolares. Demonstração com dados fictícios, para revisão da equipe do PI VI."/>
  <link rel="canonical" href="https://rbx.ia.br/estudos/historicos-escolares/"/>
</svelte:head>

<a class="skip-link" href="#conteudo">Ir para o conteúdo</a>
<div class="demo-bar"><span class="demo-dot"></span><strong>Ambiente de demonstração</strong><span>Dados fictícios · salvos somente neste navegador</span><button onclick={()=>navigate('about')}>Entenda o protótipo <span aria-hidden="true">↗</span></button></div>
<div class="app-shell">
  <aside class="sidebar" aria-label="Navegação da plataforma">
    <a class="brand" href="/estudos/historicos-escolares/" onclick={event=>{event.preventDefault();navigate('list');}}><span class="brand-symbol"><Icon name="document" size={27}/></span><span>Secretaria<br/><strong>Digital</strong></span></a>
    <div class="project-tag">PI VI <span>•</span> GRUPO 19</div>
    <div class="nav-caption">ÁREA DE TRABALHO</div>
    <nav aria-label="Principal">
      <button class:active={view==='list'||view==='detail'} onclick={()=>navigate('list')}><Icon name={staff?'folder':'grid'}/>{staff?'Fila da secretaria':'Minhas solicitações'}<span class="nav-count">{all.length}</span></button>
      {#if !staff}<button class:active={view==='new'} onclick={()=>{purpose='';year=person.year;confirmed=false;navigate('new');}}><Icon name="plus"/>Nova solicitação</button>{/if}
      <button class:active={view==='about'} onclick={()=>navigate('about')}><Icon name="info"/>Sobre o protótipo</button>
    </nav>
    <div class="sidebar-guide"><Icon name="school" size={25}/><h3>Um pedido, um caminho claro.</h3><p>Acompanhe cada etapa sem perder o histórico do atendimento.</p></div>
    <div class="sidebar-bottom"><span class="tiny-label">PROJETO ACADÊMICO</span><p>Históricos escolares<br/>Engenharia de Computação</p><span class="version">PRÉVIA 0.1 · NOV/2026</span></div>
  </aside>
  <div class="workspace">
    <header class="topbar"><span class="breadcrumb">Secretaria Digital <span>/</span> {staff?'Atendimento':'Portal do solicitante'}</span><div class="profile"><span class="avatar">{person.initials}</span><label for="profile">Perfil de demonstração<select id="profile" bind:value={actor} onchange={switchProfile}><option value="ana">Ana Exemplo · solicitante</option><option value="bruno">Bruno Exemplo · solicitante</option><option value="secretaria">Secretaria · atendimento</option></select></label></div></header>
    <main id="conteudo" tabindex="-1" bind:this={main}>
      {#if storageWarning}<div class="alert warning" role="status"><Icon name="info"/>{storageWarning}</div>{/if}
      {#if notice}<div class="alert success" role="status"><Icon name="check"/>{notice}</div>{/if}
      {#if error}<div class="alert failure" role="alert"><Icon name="info"/>{error}</div>{/if}
      {#if !ready}<div class="loading" role="status">Preparando sua demonstração…</div>
      {:else if view==='list'}
        <div class="page-heading"><div><div class="eyebrow">{staff?'ATENDIMENTO ESCOLAR':'SEU ATENDIMENTO, MAIS SIMPLES'}</div><h1>{staff?'Fila da secretaria':`Olá, ${person.name.split(' ')[0]}.`}</h1><p>{staff?'Organize os pedidos e mantenha o solicitante informado.':'Solicite seu histórico e acompanhe cada etapa em um só lugar.'}</p></div>{#if !staff}<button class="button primary" onclick={()=>{purpose='';year=person.year;confirmed=false;navigate('new');}}><Icon name="plus"/>Nova solicitação</button>{/if}</div>
        <section class="stats" aria-label="Resumo dos pedidos"><div class="stat"><span class="stat-icon blue"><Icon name="folder"/></span><div><span>Em andamento</span><strong>{inProgress.toString().padStart(2,'0')}</strong></div><span class="stat-detail">Recebidos e em análise</span></div><div class="stat"><span class="stat-icon green"><Icon name="check"/></span><div><span>Disponíveis</span><strong>{available.toString().padStart(2,'0')}</strong></div><span class="stat-detail">Exemplos para baixar</span></div><div class="stat"><span class="stat-icon amber"><Icon name="message"/></span><div><span>Precisam de informação</span><strong>{pending.toString().padStart(2,'0')}</strong></div><span class="stat-detail">Pendências para resolver</span></div></section>
        <div class="content-grid"><section class="panel requests-panel"><div class="panel-heading"><div><h2>{staff?'Solicitações recebidas':'Suas solicitações'}</h2><p>O andamento de cada pedido, sem perder os detalhes.</p></div><span class="subtle-count">{all.length} pedidos</span></div><div class="filters"><div class="search-field"><Icon name="search" size={18}/><input aria-label="Buscar solicitação" bind:value={search} placeholder={staff?'Buscar protocolo ou solicitante':'Buscar por protocolo ou finalidade'}/></div><select aria-label="Filtrar por situação" bind:value={filter}><option value="todos">Todas as situações</option>{#each Object.entries(STATES) as [key,value]}<option value={key}>{value.label}</option>{/each}</select></div><div class="table-wrap"><table><thead><tr><th>Solicitação</th><th>Situação</th><th>Atualização</th><th><span class="sr-only">Ação</span></th></tr></thead><tbody>{#each filtered as r}<tr><td><button class="request-title" onclick={()=>openRequest(r)} aria-label={`Abrir solicitação ${r.id}`}>Histórico escolar <span>↗</span></button><span class="protocol">{r.id}</span><span class="row-subtitle">{staff?PEOPLE[r.owner].name:r.purpose} · {r.year}</span></td><td><span class={`badge ${STATES[r.status].tone}`}><span></span>{STATES[r.status].label}</span></td><td class="date-cell">{date(r.updated)}</td><td><button class="icon-button" onclick={()=>openRequest(r)} aria-label={`Ver detalhes de ${r.id}`}><Icon name="arrow" size={18}/></button></td></tr>{/each}</tbody></table>{#if !filtered.length}<div class="empty"><Icon name="search" size={32}/><h3>Nenhum pedido encontrado</h3><p>Tente outra busca ou situação.</p><button class="button secondary" onclick={()=>{search='';filter='todos';}}>Limpar filtros</button></div>{/if}</div><div class="table-footer"><Icon name="clock" size={15}/><span>As datas registram o andamento desta demonstração.</span></div></section>
          <aside class="right-rail"><section class="panel guide-panel"><span class="eyebrow">COMECE POR AQUI</span><h2>Experimente o fluxo</h2><ol class="steps"><li><span>1</span><div><strong>Abra uma solicitação</strong><p>Use o perfil Ana ou Bruno e guarde o protocolo.</p></div></li><li><span>2</span><div><strong>Veja o outro lado</strong><p>Troque para Secretaria e inicie a análise do pedido.</p></div></li><li><span>3</span><div><strong>Acompanhe a mudança</strong><p>Volte ao solicitante para conferir o histórico e o documento.</p></div></li></ol><button class="text-button" onclick={()=>navigate('about')}>Ver o que já funciona <Icon name="arrow" size={16}/></button></section><section class="small-note"><Icon name="shield" size={20}/><div><strong>Uma prévia para construir juntos</strong><p>Os perfis são simulados. Esta versão não recebe pedidos reais nem emite históricos oficiais.</p></div></section></aside>
        </div>
      {:else if view==='new'}
        <button class="back-link" onclick={()=>navigate('list')}><Icon name="back" size={17}/>Voltar às solicitações</button><div class="page-heading"><div><div class="eyebrow">HISTÓRICO ESCOLAR</div><h1>Nova solicitação</h1><p>Preencha os dados do exemplo para iniciar o atendimento.</p></div></div><div class="form-layout"><section class="panel form-panel"><div class="section-number">01 <span>Dados do pedido</span></div><form onsubmit={submit}><div class="identity-card"><span class="avatar">{person.initials}</span><div><strong>{person.name}</strong><span>Solicitante fictício · perfil de demonstração</span></div><span class="badge green">Exemplo</span></div><label class="field" for="year">Ano de conclusão<span>Use um ano entre 2000 e 2026.</span><input id="year" type="text" inputmode="numeric" pattern={'20[0-9]{2}'} maxlength="4" required bind:value={year} oninput={event=>year=event.currentTarget.value}/></label><label class="field" for="purpose">Finalidade do documento<select id="purpose" required bind:value={purpose}><option value="" disabled>Selecione uma finalidade</option>{#each PURPOSES as item}<option>{item}</option>{/each}</select></label><div class="informative"><Icon name="info"/><p>Não é necessário anexar documentos nesta prévia. Os dados do solicitante são exemplos fixos e nenhum atendimento será encaminhado à escola.</p></div><label class="checkbox-field"><input type="checkbox" bind:checked={confirmed} required/><span>Entendo que este é um teste com dados fictícios, sem solicitação real à escola.</span></label><div class="form-actions"><button type="button" class="button secondary" onclick={()=>navigate('list')}>Voltar</button><button class="button primary" type="submit">Registrar solicitação <Icon name="arrow" size={18}/></button></div></form></section><aside class="panel guide-panel"><Icon name="document" size={28}/><h2>O que acontece depois?</h2><p>Você recebe um protocolo e o pedido entra na fila da secretaria de demonstração.</p><hr/><h3>Sem prazo automático</h3><p>O prazo de atendimento precisa ser definido com a escola. Esta prévia não promete uma data de emissão.</p><hr/><h3>Evite pedidos repetidos</h3><p>Se já houver um pedido igual em andamento, você será levado ao protocolo existente.</p></aside></div>
      {:else if view==='detail' && request}
        <button class="back-link" onclick={()=>navigate('list')}><Icon name="back" size={17}/>Voltar às solicitações</button><div class="page-heading"><div><div class="eyebrow">{request.id}</div><h1>Histórico escolar</h1><p>{PEOPLE[request.owner].name} <span class="middle-dot">·</span> Ano de conclusão {request.year}</p></div><span class={`badge large ${STATES[request.status].tone}`}><span></span>{STATES[request.status].label}</span></div><div class="detail-layout"><div class="detail-main"><section class="panel detail-summary"><div><span class="tiny-label">FINALIDADE</span><strong>{request.purpose}</strong></div><div><span class="tiny-label">SOLICITADO EM</span><strong>{date(request.created)}</strong></div></section>
        {#if request.status==='pendente'}<section class="panel pending-panel"><div class="panel-heading"><Icon name="message"/><h2>Uma informação precisa ser confirmada</h2></div><p>{[...request.history].reverse().find(h=>h.status==='pendente')?.note}</p>{#if !staff}<form onsubmit={reply}><label class="field" for="reply-year">Confirme o ano de conclusão<input id="reply-year" type="text" inputmode="numeric" pattern={'20[0-9]{2}'} maxlength="4" bind:value={replyYear} oninput={event=>replyYear=event.currentTarget.value} required/></label><button class="button primary">Confirmar e enviar à análise <Icon name="arrow" size={17}/></button></form>{:else}<p class="muted">O solicitante pode confirmar o ano em seu próprio perfil de demonstração.</p>{/if}</section>{/if}
        {#if request.status==='disponivel'}<section class="panel download-panel"><span class="document-tile"><Icon name="document" size={30}/></span><div><h2>Documento de demonstração disponível</h2><p>PDF ilustrativo, sem validade acadêmica ou legal.</p><button class="button primary" onclick={pdf}><Icon name="download" size={17}/>Baixar exemplo em PDF</button></div></section>{/if}
        {#if request.status==='cancelado'}<div class="alert warning"><Icon name="info"/>Este pedido foi encerrado. O histórico permanece disponível para consulta.</div>{/if}
        <section class="panel timeline-panel"><div class="panel-heading"><div><h2>Histórico do atendimento</h2><p>Cada etapa registrada, do pedido à conclusão.</p></div><Icon name="clock"/></div><ol class="timeline">{#each [...request.history].reverse() as h,index}<li class:latest={index===0}><span class="timeline-dot"><Icon name={h.status==='disponivel'?'check':'clock'} size={13}/></span><div><div class="event-title"><strong>{STATES[h.status].label}</strong><time datetime={h.at}>{time(h.at)}</time></div><p>{h.note}</p><span class="event-actor">{PEOPLE[h.actor].name}</span></div></li>{/each}</ol></section></div><aside class="detail-aside">{#if staff}<section class="panel guide-panel"><span class="eyebrow">ÁREA DA SECRETARIA</span><h2>Próxima etapa</h2>{#if allowedTransitions(request.status).length}<label class="field" for="note">Observação ao solicitante<textarea id="note" bind:value={note} maxlength="500" rows="4" placeholder="Explique a atualização ou a informação necessária."></textarea></label><span class="char-count">{note.length}/500</span><div class="action-stack">{#each allowedTransitions(request.status) as target}<button class={`button ${target==='cancelado'?'danger-outline':'primary'}`} onclick={()=>change(target)}>{actions[target]}</button>{/each}</div><p class="fine-print">Para solicitar informação, preencha a observação. Disponibilizar gera apenas um PDF ilustrativo.</p>{:else}<p>Este pedido está concluído. Seu histórico permanece preservado.</p>{/if}</section>{:else}<section class="panel guide-panel"><span class="eyebrow">ACOMPANHAMENTO</span><h2>{request.status==='disponivel'?'Seu exemplo está pronto':'Você está acompanhando'}</h2><p>{request.status==='pendente'?'Confira a orientação da secretaria e confirme o ano informado.':'As atualizações feitas pela secretaria aparecem neste pedido e ficam registradas no histórico.'}</p><hr/><p class="fine-print">Para experimentar o atendimento, troque para o perfil Secretaria no topo da página. Essa troca é uma simulação, sem autenticação real.</p></section>{/if}<div class="small-note"><Icon name="bell"/><div><strong>Atualizações dentro da plataforma</strong><p>Nesta prévia, o histórico informa as mudanças. Não há envio de e-mail ou WhatsApp.</p></div></div></aside></div>
      {:else if view==='about'}
        <div class="page-heading"><div><div class="eyebrow">PI VI · TURMA 002 · GRUPO 19</div><h1>Uma prévia para revisar juntos.</h1><p>Da proposta do relatório a um fluxo que a equipe pode experimentar.</p></div><span class="version-tag">VERSÃO 0.1</span></div><div class="about-hero"><Icon name="school" size={42}/><div><h2>Históricos escolares, com mais clareza no atendimento.</h2><p>Protótipo acadêmico inspirado no relatório-base de Laerte Rodrigues Vieira Junior, com implementação inicial de Leandro Damasio assistida por IA. A adequação à escola e a incorporação à entrega final dependem da revisão da equipe e do orientador.</p></div></div><div class="about-grid"><section class="panel guide-panel"><span class="eyebrow">IMPLEMENTADO NESTA PRÉVIA</span><h2>O que você pode testar</h2><ul class="feature-list"><li><Icon name="check"/>Pedidos com protocolo e detecção de duplicidade</li><li><Icon name="check"/>Consulta por perfil fictício e filtros de situação</li><li><Icon name="check"/>Análise, pendência e confirmação de informação</li><li><Icon name="check"/>Histórico de eventos e PDF de demonstração</li><li><Icon name="check"/>Persistência local, exportação e restauração</li></ul></section><section class="panel guide-panel"><span class="eyebrow">PRÓXIMAS ETAPAS</span><h2>O que ainda será validado</h2><p>Fluxo, requisitos e prazos com a escola; testes com participantes; autenticação e permissões no servidor; documentos privados; notificações e operação.</p><p><strong>Stack:</strong> SvelteKit. API em Go e banco PostgreSQL são a direção prevista para a etapa com backend; não estão em uso nesta prévia.</p><p>Os testes locais não comprovam redução de filas, implantação na escola ou validação com a comunidade.</p></section></div><section class="panel storage-panel"><div><h2>Sua sessão de demonstração</h2><p>Os pedidos ficam neste navegador. Trocar de aparelho não compartilha os dados. Todos os perfis e registros do exemplo são acessíveis a quem usa esta sessão.</p><p class="fine-print">Exporte antes de reiniciar. Restaurar uma cópia substitui os registros locais atuais. Não use dados ou documentos reais.</p></div><div class="storage-actions"><button class="button secondary" onclick={exportDemo}><Icon name="download" size={17}/>Exportar exemplos</button><button class="button secondary" onclick={()=>importInput?.click()}><Icon name="folder" size={17}/>Restaurar cópia</button><input class="sr-only" type="file" accept="application/json,.json" bind:this={importInput} onchange={importDemo} aria-label="Selecionar cópia da demonstração"/>{#if resetConfirm}<div class="reset-confirm"><p>Reiniciar remove os pedidos criados neste navegador e recarrega os exemplos.</p><button class="button danger-outline" onclick={reset}>Confirmar reinício</button><button class="text-button" onclick={()=>resetConfirm=false}>Manter meus pedidos</button></div>{:else}<button class="text-button" onclick={()=>resetConfirm=true}><Icon name="refresh" size={16}/>Reiniciar demonstração</button>{/if}</div></section>
      {/if}
      <footer class="page-footer"><span>Secretaria Digital <span>·</span> Projeto Integrador VI</span><span>Protótipo acadêmico · infraestrutura de demonstração RBX</span></footer>
    </main>
  </div>
</div>
