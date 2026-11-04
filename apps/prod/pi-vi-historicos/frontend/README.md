# Secretaria Digital — PI VI, Grupo 19

Prévia acadêmica em SvelteKit para revisão com Laerte. O relatório-base define o problema de solicitação e disponibilização de históricos escolares; esta implementação demonstra o fluxo com perfis e documentos fictícios.

## Executar

Node.js 24; dependências fixadas em `package-lock.json`.

```sh
npm ci
npm run dev
npm run check
npm test
npm run build
npm run preview
```

## Recorte implementado

- Dois solicitantes fictícios e secretaria, selecionáveis no topo da página.
- Abertura com protocolo, validação e detecção de duplicidade de pedidos em andamento.
- Fila com busca e filtros, análise, pendências, confirmação do ano e disponibilização.
- Histórico de transições e download de um PDF sem validade acadêmica.
- Armazenamento no navegador, exportação e restauração de cópia JSON validada.
- Layout responsivo e navegação por teclado.

Os perfis são uma simulação, **não autenticação**. Todo o estado está acessível a quem usa o navegador, inclusive via exportação. O filtro da interface e as regras locais não constituem controle de acesso de servidor. Não inserir dados reais. Não há cadastro, upload de documentos reais, API, banco, e-mail ou integração com escola.

## Arquitetura e continuidade

SvelteKit 3, Svelte 5, `adapter-static`. As regras independentes da interface estão em `src/lib/workflow.js`. O frontend não exige processo Node em produção; o resultado `build/` pode ser servido por nginx.

Backend, se incorporado: **Go**, com **PostgreSQL**. Autenticação, autorização por recurso, armazenamento privado de documentos, validação de anexos, auditoria no servidor e notificações precisam ser implementados e testados antes de uso real. As regras locais devem migrar para a API, e o frontend não poderá ser a autoridade das permissões.

### Falhas e limites

- Sem JavaScript, o HTML inicial permanece visível, mas as ações não funcionam.
- Se o armazenamento local falhar, a interface avisa e mantém a sessão em memória; é possível exportar uma cópia.
- Os dados não são sincronizados entre aparelhos. Restaurar uma cópia substitui a sessão atual.
- Esta versão não demonstra implantação na escola, validação comunitária, economia de tempo ou redução de filas.
- Reiniciar a demonstração requer confirmação e recarrega os exemplos; exportar antes se houver evidências a preservar.

Créditos: relatório-base de Laerte Rodrigues Vieira Junior; implementação inicial de Leandro Damasio com assistência de IA. Contribuições e autoria da entrega final devem ser acordadas com a equipe e o orientador.
