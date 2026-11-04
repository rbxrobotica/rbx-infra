// This module governs a browser-local demonstration. It is not an authorization boundary.
export const STORAGE_KEY = 'pi_vi_historicos_demo_v1';
export const PEOPLE = {
  ana: { name: 'Ana Exemplo', initials: 'AE', role: 'Solicitante', year: '2024' },
  bruno: { name: 'Bruno Exemplo', initials: 'BE', role: 'Solicitante', year: '2023' },
  secretaria: { name: 'Secretaria de demonstração', initials: 'SE', role: 'Secretaria' }
};
export const STATES = {
  recebido: { label: 'Recebido', tone: 'blue' },
  analise: { label: 'Em análise', tone: 'amber' },
  pendente: { label: 'Aguardando informação', tone: 'amber' },
  disponivel: { label: 'Disponível', tone: 'green' },
  cancelado: { label: 'Cancelado', tone: 'gray' }
};
export const PURPOSES = ['Matrícula em outra instituição', 'Ingresso no ensino superior', 'Processo seletivo', 'Conferência do documento'];
const EDGES = { recebido: ['analise', 'cancelado'], analise: ['pendente', 'disponivel', 'cancelado'], pendente: ['analise', 'cancelado'], disponivel: [], cancelado: [] };
const ev = (at, status, actor, note) => ({ at, status, actor, note });

export function seed() {
  return { version: 1, requests: [
    { id: 'DEMO-2026-0001', owner: 'ana', year: '2024', purpose: PURPOSES[0], status: 'analise', created: '2026-11-03T12:10:00Z', updated: '2026-11-04T11:30:00Z', history: [ev('2026-11-03T12:10:00Z','recebido','ana','Solicitação registrada.'),ev('2026-11-04T11:30:00Z','analise','secretaria','Pedido recebido pela secretaria para conferência.')] },
    { id: 'DEMO-2026-0002', owner: 'ana', year: '2022', purpose: PURPOSES[3], status: 'disponivel', created: '2026-11-02T13:20:00Z', updated: '2026-11-03T15:00:00Z', history: [ev('2026-11-02T13:20:00Z','recebido','ana','Solicitação registrada.'),ev('2026-11-03T12:00:00Z','analise','secretaria','Conferência iniciada.'),ev('2026-11-03T15:00:00Z','disponivel','secretaria','Documento ilustrativo disponível para demonstração.')] },
    { id: 'DEMO-2026-0003', owner: 'bruno', year: '2023', purpose: PURPOSES[1], status: 'pendente', created: '2026-11-03T14:00:00Z', updated: '2026-11-04T12:00:00Z', history: [ev('2026-11-03T14:00:00Z','recebido','bruno','Solicitação registrada.'),ev('2026-11-03T15:00:00Z','analise','secretaria','Conferência iniciada.'),ev('2026-11-04T12:00:00Z','pendente','secretaria','Confirme o ano de conclusão informado no pedido de demonstração.')] },
    { id: 'DEMO-2026-0004', owner: 'bruno', year: '2021', purpose: PURPOSES[2], status: 'recebido', created: '2026-11-04T12:30:00Z', updated: '2026-11-04T12:30:00Z', history: [ev('2026-11-04T12:30:00Z','recebido','bruno','Solicitação registrada.')] }
  ] };
}

export function validateSnapshot(value) {
  if (!value || value.version !== 1 || !Array.isArray(value.requests) || value.requests.length > 500) throw new Error('A cópia local não é compatível com esta versão.');
  const ids = new Set();
  for (const r of value.requests) {
    if (!r || !/^DEMO-2026-\d{4,}$/.test(r.id) || ids.has(r.id) || !['ana','bruno'].includes(r.owner) || !STATES[r.status] || !/^20\d{2}$/.test(r.year) || !PURPOSES.includes(r.purpose) || !Number.isFinite(Date.parse(r.created)) || !Number.isFinite(Date.parse(r.updated)) || !Array.isArray(r.history) || !r.history.length || r.history.length > 200) throw new Error('A cópia local contém um pedido inválido.');
    for (const h of r.history) if (!h || !Number.isFinite(Date.parse(h.at)) || !STATES[h.status] || !PEOPLE[h.actor] || typeof h.note !== 'string' || h.note.length > 500) throw new Error('O histórico local não é válido.');
    if (r.history[0].status !== 'recebido' || r.history.at(-1).status !== r.status) throw new Error('Histórico inconsistente.');
    ids.add(r.id);
  }
  return structuredClone(value);
}
export function visibleRequests(state, actor) {
  if (!PEOPLE[actor]) throw new Error('Perfil de demonstração inválido.');
  return state.requests.filter(r => actor === 'secretaria' || r.owner === actor).toSorted((a,b) => b.updated.localeCompare(a.updated));
}
export function createRequest(state, actor, input, at = new Date().toISOString()) {
  if (!['ana','bruno'].includes(actor)) throw new Error('Selecione um solicitante para abrir um pedido.');
  if (!/^20\d{2}$/.test(input.year) || +input.year < 2000 || +input.year > 2026) throw new Error('Informe um ano entre 2000 e 2026.');
  if (!PURPOSES.includes(input.purpose)) throw new Error('Escolha a finalidade do pedido.');
  if (state.requests.length >= 500) throw new Error('Limite da demonstração atingido. Exporte os registros ou reinicie os exemplos.');
  const duplicate = state.requests.find(r => r.owner === actor && r.year === input.year && r.purpose === input.purpose && !['cancelado','disponivel'].includes(r.status));
  if (duplicate) return { state, request: duplicate, duplicate: true };
  const next = Math.max(0, ...state.requests.map(r => Number(r.id.split('-').at(-1)))) + 1;
  const request = { id: `DEMO-2026-${String(next).padStart(4,'0')}`, owner: actor, year: input.year, purpose: input.purpose, status: 'recebido', created: at, updated: at, history: [ev(at, 'recebido', actor, 'Solicitação registrada.')] };
  return { state: { ...state, requests: [...state.requests, request] }, request, duplicate: false };
}
export function transition(state, actor, id, target, note = '', at = new Date().toISOString()) {
  const request = state.requests.find(r => r.id === id);
  if (!request) throw new Error('Solicitação não encontrada.');
  if (actor !== 'secretaria') throw new Error('Troque para o perfil Secretaria para realizar esta operação na demonstração.');
  if (!EDGES[request.status]?.includes(target)) throw new Error('Esta mudança não está disponível no estado atual.');
  const clean = note.trim();
  if (clean.length > 500) throw new Error('Use até 500 caracteres na observação.');
  if (target === 'pendente' && !clean) throw new Error('Explique a informação necessária ao solicitante.');
  const updated = { ...request, status: target, updated: at, history: [...request.history, ev(at, target, actor, clean || `Situação alterada para ${STATES[target].label.toLowerCase()}.`)] };
  return { ...state, requests: state.requests.map(r => r.id === id ? updated : r) };
}
export function replyPending(state, actor, id, year, at = new Date().toISOString()) {
  const r = state.requests.find(r => r.id === id);
  if (!r || actor !== r.owner || r.status !== 'pendente') throw new Error('Não há pendência disponível para este perfil.');
  if (!/^20\d{2}$/.test(year) || +year < 2000 || +year > 2026) throw new Error('Informe um ano entre 2000 e 2026.');
  const updated = { ...r, year, status: 'analise', updated: at, history: [...r.history, ev(at,'analise',actor,`Ano de conclusão confirmado como ${year}. Pedido devolvido à análise.`)] };
  return { ...state, requests: state.requests.map(x => x.id === id ? updated : x) };
}
export function allowedTransitions(status) { return EDGES[status] || []; }

// A real PDF with a conspicuous demo notice, no real academic or personal data.
export function demonstrationPdf(request, actor) {
  if ((actor !== request.owner && actor !== 'secretaria') || request.status !== 'disponivel') throw new Error('O documento não está disponível para este perfil.');
  const lines = ['DOCUMENTO DE DEMONSTRACAO', 'SEM VALIDADE ACADEMICA OU LEGAL', '', `Protocolo: ${request.id}`, `Pessoa ficticia: ${PEOPLE[request.owner].name}`, `Ano informado: ${request.year}`, '', 'Este arquivo ilustra o fluxo de disponibilizacao.', 'Nao e um historico escolar. Nenhum documento oficial foi emitido.', '', 'Projeto Integrador VI - Grupo 19 - Prototipo para revisao'];
  const escape = s => s.replace(/[\\()]/g, '\\$&');
  const content = `BT /F1 12 Tf 50 780 Td 22 TL ${lines.map((s,i) => `${i ? 'T* ' : ''}(${escape(s)}) Tj`).join('\n')} ET`;
  const objects = ['<< /Type /Catalog /Pages 2 0 R >>','<< /Type /Pages /Kids [3 0 R] /Count 1 >>','<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>','<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',`<< /Length ${content.length} >>\nstream\n${content}\nendstream`];
  let pdf = '%PDF-1.4\n'; const offsets = [0];
  for (const [i,obj] of objects.entries()) { offsets.push(pdf.length); pdf += `${i+1} 0 obj\n${obj}\nendobj\n`; }
  const xref = pdf.length;
  pdf += `xref\n0 6\n0000000000 65535 f \n${offsets.slice(1).map(n=>String(n).padStart(10,'0')+' 00000 n \n').join('')}trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return pdf;
}
