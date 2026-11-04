import test from 'node:test';
import assert from 'node:assert/strict';
import { seed, PURPOSES, validateSnapshot, visibleRequests, createRequest, transition, replyPending, demonstrationPdf } from '../src/lib/workflow.js';

test('demonstração: solicitantes veem apenas seus exemplos; secretaria vê a fila completa',()=>{
  const state=seed();
  assert.equal(visibleRequests(state,'ana').length,2);
  assert.equal(visibleRequests(state,'bruno').length,2);
  assert.equal(visibleRequests(state,'secretaria').length,4);
  assert.throws(()=>visibleRequests(state,'outro'));
});
test('fluxo completo com protocolo, pendência, resposta e PDF apenas após disponibilização',()=>{
  const {state,request}=createRequest(seed(),'ana',{year:'2025',purpose:PURPOSES[2]});
  assert.equal(request.id,'DEMO-2026-0005');
  assert.throws(()=>demonstrationPdf(request,'ana'));
  assert.throws(()=>transition(state,'ana',request.id,'disponivel'));
  assert.throws(()=>transition(state,'secretaria',request.id,'disponivel'));
  let next=transition(state,'secretaria',request.id,'analise');
  assert.throws(()=>transition(next,'secretaria',request.id,'pendente','   '));
  next=transition(next,'secretaria',request.id,'pendente','Confirme o ano.');
  assert.throws(()=>replyPending(next,'bruno',request.id,'2024'));
  next=replyPending(next,'ana',request.id,'2024');
  assert.equal(next.requests.at(-1).year,'2024');
  next=transition(next,'secretaria',request.id,'disponivel');
  const result=next.requests.at(-1);
  assert.equal(result.history.length,5);
  assert.equal(result.status,'disponivel');
  assert.match(demonstrationPdf(result,'ana'),/^%PDF-1.4/);
  assert.match(demonstrationPdf(result,'ana'),/SEM VALIDADE/);
  assert.throws(()=>demonstrationPdf(result,'bruno'));
  assert.throws(()=>transition(next,'secretaria',request.id,'analise'));
  assert.equal(validateSnapshot(next).requests.length,5);
});
test('envio repetido preserva o protocolo e o estado de origem',()=>{
  const original=seed();
  const a=createRequest(original,'ana',{year:'2025',purpose:PURPOSES[1]});
  const b=createRequest(a.state,'ana',{year:'2025',purpose:PURPOSES[1]});
  assert.equal(b.duplicate,true);
  assert.equal(b.request.id,a.request.id);
  assert.equal(b.state.requests.length,5);
  assert.equal(original.requests.length,4);
});
test('validação recusa entradas fora do recorte e cópias inconsistentes',()=>{
  for(const year of ['1999','2027','NaN','','2024.5']) assert.throws(()=>createRequest(seed(),'ana',{year,purpose:PURPOSES[0]}));
  assert.throws(()=>createRequest(seed(),'secretaria',{year:'2024',purpose:PURPOSES[0]}));
  assert.throws(()=>createRequest(seed(),'ana',{year:'2024',purpose:'qualquer'}));
  assert.throws(()=>validateSnapshot({version:2,requests:[]}));
  const bad=seed();bad.requests[1].id=bad.requests[0].id;
  assert.throws(()=>validateSnapshot(bad));
  const inconsistent=seed();inconsistent.requests[0].status='disponivel';
  assert.throws(()=>validateSnapshot(inconsistent));
  const copied=validateSnapshot(seed());copied.requests.length=0;
  assert.equal(seed().requests.length,4);
});
