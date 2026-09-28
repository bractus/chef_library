# Quickstart: validar a Aba de Ingestão de Livros

Roteiro para provar, de ponta a ponta, que a feature atende à [spec](spec.md). Os contratos estão em [contracts/](contracts/) e os estados e códigos em [data-model.md](data-model.md).

## Pré-requisitos

- Docker Desktop com espaço livre para a imagem (a imagem do backend cresce cerca de 50 MB por causa do Tesseract).
- `.env` com `OPENROUTER_API_KEY` válida (embeddings e metadados).
- `data/` com o acervo atual (índice de cerca de 97 mil trechos).
- **Backup antes de testar**: `cp -R data data.bak`. Os testes escrevem no acervo real e não há remoção pela aba (Clarifications Q2).
- Livros de teste em `~/chef-test/` (não versionados):
  - `receitas.pdf`: livro de receitas com texto selecionável, de 150 a 300 páginas
  - `receitas.epub`: **a mesma obra** de `receitas.pdf` em EPUB (para o cenário Q7)
  - `outro.docx`, `outro.txt`, `outro.md`: livros curtos de receitas **diferentes entre si**
  - `escaneado.pdf`: livro de receitas digitalizado, só imagem
  - `parcial.pdf`: PDF com texto em cerca de metade das páginas
  - `manual.pdf`: livro que não é de culinária (ex.: manual técnico)
  - `quebrado.pdf`: `head -c 5000 receitas.pdf > quebrado.pdf`
  - Uma cópia de um `.md` que já está no acervo (qualquer arquivo cujo md5 esteja em `data/index/cache/processed.json`), se você tiver os originais

## Subir

```bash
docker compose up --build
# frontend: http://localhost:5173   ·   API: http://localhost:8000
```

Fora do Docker (desenvolvimento): `brew install tesseract tesseract-lang`, `pip install -r requirements.txt -r requirements-dev.txt` e `uvicorn backend.api.main:app --port 8000` **sem** `--workers` (o worker da fila vive no processo da API, ver research R3).

## Testes automatizados

```bash
pytest tests/ -q
```

Esperado: todos passam. Os testes cobrem os extratores por formato, a regra de duplicata, o commit com interrupção simulada e os endpoints de `contracts/ingest-api.md`.

## Cenários manuais

Anote antes: o número de livros e de trechos no medidor do topo (`GET /api/stats`).

| # | Cenário | Passos | Resultado esperado | Spec |
|---|---------|--------|--------------------|------|
| Q1 | Livro novo em PDF | Aba **Adicionar livros** → arrastar `receitas.pdf` | Estados na fila → processando (com estágios) → **adicionado**, com título, idioma e nº de trechos, em **≤ 5 min**. O medidor sobe +1 livro sem recarregar a página. | US1 AC1, AC3; FR-011, FR-014; SC-001 |
| Q2 | Consulta ao livro novo | Aba **Consultar** → perguntar sobre uma receita que só existe em `receitas.pdf` | A resposta cita o livro novo como fonte, e ele aparece no filtro de livros | US1 AC2 |
| Q3 | Consulta durante a ingestão | Enviar `outro.docx` e, enquanto ele processa, fazer 3 perguntas | As 3 respostas chegam normalmente, sem erro 5xx | US1 AC4; SC-004 |
| Q4 | Vários formatos | Enviar `outro.txt` e `outro.md` | Os dois ficam **adicionados** e consultáveis | US3 AC1; SC-008 |
| Q5 | Formato não suportado | Selecionar um `.xlsx` ou `.jpg` | Recusado **antes do envio**, com a lista de formatos aceitos. Nada aparece no histórico. | US3 AC2; FR-004 |
| Q6 | Lote misto | Enviar juntos `manual.pdf`, `quebrado.pdf` e a cópia de um `.md` já existente | Cada um com seu estado: **descartado** (não culinário), **erro** (corrompido, sem "tentar de novo"), **já existe** (instantâneo) | US4 AC1–AC4; SC-007 |
| Q7 | Duplicata em outro formato | Depois do Q1, enviar `receitas.epub` | **Já existe**, "muito parecido com «<título do Q1>»". Os trechos em `/api/stats` não mudam. | FR-008; SC-005 |
| Q8 | Sair e voltar | Enviar `escaneado.pdf`, trocar para a aba Consultar, recarregar a página e voltar | O estado continua avançando e aparece atualizado | US4 AC5; FR-010 |
| Q9 | PDF escaneado com OCR | `escaneado.pdf` chega a **sem texto extraível** → clicar **Tentar com OCR** → confirmar o aviso | Volta à fila sem reenvio e mostra o progresso "página x/y". Fica **adicionado** em ≤ 30 min, e uma pergunta sobre uma receita dele cita o livro. | FR-021–FR-024; SC-009 |
| Q10 | PDF parcial | Enviar `parcial.pdf` | Para em **sem texto extraível** com "N de M páginas sem texto" e duas ações. **Continuar sem OCR** → adicionado com o nº de páginas sem texto informado. | Edge case "PDF parcialmente escaneado" |
| Q11 | Reinício | `docker compose restart backend` | Os livros de Q1, Q4 e Q9 continuam no medidor e nas consultas, e o histórico mostra todos os envios com data, formato e resultado | US2 AC1; US5; SC-003 |
| Q12 | Rebuild da imagem | `docker compose down && docker compose up --build` | O mesmo que Q11. Os originais estão em `./books/`. | US2 AC2; FR-017 |
| Q13 | Interrupção no meio | Enviar `outro.docx` (ou um livro grande) e rodar `docker compose kill backend` durante "processando" → `docker compose up` | O acervo sobe íntegro (sem o erro "Indice inconsistente"). O job aparece como **erro: interrompido** com **Tentar de novo**, e o retry o leva a adicionado. | US2 AC3; FR-015, FR-018 |
| Q14 | Sem chave de embeddings | Remover `OPENROUTER_API_KEY` do `.env` e reiniciar | A aba mostra o aviso e o envio fica desabilitado. O histórico continua visível. A consulta segue funcionando se houver outro provedor de chat. | R11 |
| Q15 | Idioma da interface | Alternar PT/EN no seletor | Todos os textos da aba (incluindo estados e motivos) mudam de idioma | FR-020 |
| Q16 | Rebuild pelo CLI inclui os envios | `docker compose exec backend python -m backend.ingest.build_index --no-llm --skip-embed --limit 1000` (sem `--rebuild`, para não custar caro) | O log mostra os arquivos de `books/` enviados pela aba como **já processados** (pulados pelo manifesto), sem "ignorado por não ser markdown" | FR-025, FR-026 |

| Q17 | Pasta | **Escolher pasta** (ou arrastar) numa pasta com subpasta, um `.jpg` e um `.DS_Store` | Os livros da pasta e da subpasta entram na fila; aparece "1 arquivo(s) da pasta ignorado(s)"; o arquivo oculto não aparece | FR-028 |
| Q18 | Cancelar envio | Enviar um PDF grande e, no meio da barra, clicar **Cancelar envio** | "Envio cancelado. 1 arquivo(s) não foram enviados." Nada entra na fila | FR-029 |
| Q19 | Cancelar conversão | Enviar um livro e, em "Processando", clicar **Cancelar** | "Cancelando…" e depois **Cancelado**. O medidor de livros não muda | FR-030 |
| Q20 | Obra de referência | Enviar o *Modernist Cuisine vol. 1* (314 MB) | Envio sem limite de tamanho; **Descartado** com "Obra de referência extensa (estilo enciclopédia)…" | FR-004, FR-007 |

O roteiro automatizado de navegador usado na implementação (Playwright) cobriu Q5, Q6, Q15, Q17, Q18, Q19 e o layout em 390 px de largura.

### Verificação do SC-002 (tempo independe do tamanho do acervo)

1. Com o acervo completo, envie `outro.md` e anote `finished_at - created_at` (`GET /api/ingest/jobs`).
2. Suba uma cópia com acervo vazio (`INDEX_DIR=data-empty/index`, `BOOKS_RAW_DIR=books-empty`) e envie o mesmo arquivo. Isso testa também a criação do índice a partir do zero.
3. Esperado: diferença de no máximo 20%.

## Limpeza

Restaure o backup, porque não há remoção pela aba:

```bash
docker compose down
rm -rf data && mv data.bak data
rm -f books/<arquivos de teste>
```
