---
title: Cases
description: Prova de trabalho, field notes e sistemas internos.
eyebrow: Work
lead: Prova de trabalho, field notes e sistemas internos.
---
<p>Não publicamos nomes de clientes ou detalhes sensíveis sem autorização. O que aparece aqui é público com consentimento, aprendizado de campo anonimizado ou trabalho em nossa própria infraestrutura e produtos.</p>
<h2>RBX Product Platform</h2>
<p><strong>Tipo:</strong> Sistema interno<br><strong>Indústria:</strong> Engineering infrastructure</p>
<p>Design e operação da plataforma interna que suporta Robson, Strategos, TruthMetal, Thalamus, Eden e Argos Radar. Kubernetes, GitOps, observabilidade compartilhada e identidade cross-product.</p>
<ul>
<li>Declarative infrastructure per product namespace</li>
<li>Shared identity and entitlement layer</li>
<li>Centralized logging, metrics and alerting baseline</li>
<li>Reproducible deployment pipeline from commit to production</li>
</ul>
<h2>Robson — Algorithmic Trading Engine</h2>
<p><strong>Tipo:</strong> Sistema interno<br><strong>Indústria:</strong> Financial operations</p>
<p>Case interno sobre a construção de um motor de trading direcional onde confiabilidade de execução, controles de risco e auditabilidade importam mais do que retornos em destaque.</p>
<ul>
<li>Real-time market data ingestion and signal generation</li>
<li>Automated risk checks before order execution</li>
<li>Trade journal and replay capability for post-hoc analysis</li>
<li>First live capital execution under controlled exposure</li>
</ul>
<h2>TruthMetal — Ground-Truth Control Plane</h2>
<p><strong>Tipo:</strong> Sistema interno<br><strong>Indústria:</strong> AI systems</p>
<p>Case interno sobre a necessidade de uma camada de verdade canônica quando múltiplos agentes compartilham parâmetros, decisões e critérios de avaliação.</p>
<ul>
<li>Versioned golden cases and eval suites</li>
<li>Shared parameter registry across agents</li>
<li>Regression detection for agent behavior</li>
<li>Audit trail of what changed and why</li>
</ul>
<h2>Deterministic AI attached to ERP data</h2>
<p><strong>Tipo:</strong> Field note<br><strong>Indústria:</strong> Operations / Enterprise software</p>
<p>Field note sobre integrar assistentes baseados em LLM com registros de ERP sem permitir que alucinações afetem decisões operacionais. Detalhes do cliente omitidos a pedido.</p>
<ul>
<li>Structured retrieval from ERP tables</li>
<li>Decision boundaries restricted to validated fields</li>
<li>Human confirmation gate for write operations</li>
<li>Observability of every generated recommendation</li>
</ul>
