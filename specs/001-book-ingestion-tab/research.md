# Research: Aba de Ingestão de Livros

**Feature**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md) · **Date**: 2026-09-27

Cada seção registra uma decisão, o motivo e as alternativas descartadas. Para o que existe hoje no código, os fatos citados foram verificados no repositório nesta data.

## Estado atual relevante

- `backend/ingest/build_index.py` lê só `books/*.md`. A fase 1 limpa, classifica, divide em trechos e extrai metadados. A fase 2 **regera os embeddings de todos os trechos** de `data/index/chunks_raw.jsonl` e reescreve o índice. O último build levou cerca de 25 min para 96.866 trechos.
- O índice é um `faiss.IndexFlatIP` (`data/index/chunks.faiss`, ~595 MB) com `chunks.jsonl` (~199 MB) em paralelo. `ChunkStore.load` recusa subir se `index.ntotal != len(chunks)`.
- A API (`backend/api/main.py`) carrega o `ChunkStore` uma vez (`lru_cache`) e roda num único processo uvicorn (sem `--workers`).
- `data/index/cache/processed.json` mapeia o **md5 do arquivo original** para `{n_chunks, tier, slug}` (755 entradas, nenhuma com erro).
- Embeddings: `openai/text-embedding-3-small` via OpenRouter, o mesmo modelo que gerou o índice (`manifest.json`). Sem `OPENROUTER_API_KEY` não há como gerar embeddings.
- O frontend chama a API direto em `http://localhost:8000` (`VITE_API_URL`), sem proxy nginx, então não há limite de corpo do nginx no caminho do upload.
- `books/` e `books_md/` não existem neste checkout: o acervo veio pronto em `data/`. O `docker-compose.yml` monta só `./data`.
- O chunker detecta títulos por capitalização (`_is_title_line`) e o `clean_text` não trata `#` de Markdown.

---

## R1. Como acrescentar um livro sem reprocessar o acervo (FR-009, SC-002)

**Decision**: Ingestão incremental dentro do processo da API. O worker gera embeddings só dos trechos novos, faz `index.add()` no `IndexFlatIP` já em memória, acrescenta os `Chunk` à lista do `ChunkStore` e persiste com o protocolo de commit do R2. Também acrescenta os trechos a `chunks_raw.jsonl` e registra o md5 em `processed.json`, para que o pipeline de linha de comando enxergue o livro como já processado.

**Rationale**: `IndexFlatIP` aceita `add` sem retreino. O índice já está em memória, então buscar e acrescentar custa só os vetores novos. Manter `chunks_raw.jsonl` e `processed.json` em dia preserva o contrato do CLI: uma fase 2 posterior regera o índice com os livros novos incluídos.

**Alternatives considered**:
- *Rodar `build_index` como subprocesso por livro*: a fase 2 regera tudo (~25 min e custo de API por livro). Viola SC-001 e SC-002.
- *Índice em segmentos (um arquivo FAISS por livro, busca em todos)*: evita reescrever 595 MB por commit, mas muda a busca, a carga e o formato em disco. Complexidade desnecessária no volume atual (~580 livros).
- *Trocar para outro banco vetorial*: fora do escopo e muda o formato de `data/` que o usuário distribui.

## R2. Persistência atômica e recuperação após interrupção (FR-016, FR-018, US2)

**Decision**: Commit em duas fases com diário, sob um lock de arquivo:
1. Gravar `chunks.faiss.tmp` (`faiss.write_index` do índice já acrescido) e `chunks.jsonl.tmp` (cópia do atual + linhas novas), cada um com `fsync`.
2. Gravar `data/index/commit.json` com `{job_id, n_chunks_after, files: [...]}` e `fsync`. Esse é o ponto de commit.
3. `os.replace` de cada `.tmp` sobre o arquivo final, depois append em `chunks_raw.jsonl`, atualização de `processed.json` e de `manifest.json`, e remoção de `commit.json`.

Na subida da API (e no início do CLI), a recuperação faz o seguinte: se `commit.json` existe, conclui o passo 3 (é idempotente, porque cada arquivo `.tmp` só é trocado se ainda existir, e o append em `chunks_raw.jsonl` verifica pelos ids se os trechos já estão lá); se só houver `.tmp` sem `commit.json`, apaga os `.tmp`. Jobs que estavam em `processing` viram `error` com `reason_code=interrupted`, reprocessáveis.

**Rationale**: `os.replace` é atômico por arquivo, mas há dois arquivos (índice e metadados) que precisam mudar juntos. O diário torna o conjunto atômico. A falha que o `ChunkStore.load` já detecta (`ntotal != len(chunks)`) deixa de ser alcançável por uma interrupção.

**Custo**: reescrever cerca de 800 MB por livro leva poucos segundos num SSD. É O(tamanho do acervo), mas fica pequeno perto dos 30 a 90 s típicos de uma ingestão (extração, LLM e embeddings), dentro da margem de 20% do SC-002. O R1 lista o índice segmentado como otimização futura, caso o acervo cresça uma ordem de grandeza.

**Alternatives considered**:
- *Append direto em `chunks.jsonl` com truncamento na recuperação*: exige guardar offsets e é mais frágil do que o diário.
- *SQLite para os metadados dos trechos*: muda o formato de `data/` e o `ChunkStore.load`.

## R3. Concorrência: buscas durante a ingestão, vários envios e o CLI (FR-010, FR-012, SC-004)

**Decision**:
- **Um worker em thread** dentro do processo da API, iniciado no `lifespan` do FastAPI e consumindo a fila de jobs em ordem FIFO, um de cada vez.
- **`threading.RLock` no `ChunkStore`**: `search`, `stats` e listagens pegam o lock para ler, e o `append` (add no índice e extensão das listas) pega o mesmo lock. Só o `add` em memória e o `write_index` ficam dentro do lock. A extração, o LLM e os embeddings, que são a parte cara, ficam fora dele.
- **Lock de arquivo entre processos** (`fcntl.flock` em `data/index/.lock`): o worker o segura só durante o commit, e o `build_index` o segura durante a execução inteira. Se o CLI estiver rodando, o worker espera e mostra no job o estágio `waiting_for_index_lock`.
- **Recarga após o CLI**: a API lê o `mtime` de `manifest.json` a cada requisição (é uma chamada `stat`, barata) e recarrega o `ChunkStore` se o arquivo mudou por fora (o commit do próprio worker atualiza a referência e não dispara recarga).

**Rationale**: a busca num `IndexFlat` de 97 mil vetores leva milissegundos, e segurar o lock ali não prejudica o SC-004. Um worker só elimina conflitos entre ingestões sem precisar de mais nada.

**Alternatives considered**:
- *Celery/RQ com Redis*: adiciona serviço ao docker-compose por um volume de poucos livros por vez.
- *Copy-on-write do índice (clonar e trocar)*: dobra a memória (~600 MB por cópia) a cada commit.
- *Vários workers*: as chamadas externas já são o gargalo, e o lock de commit serializaria de qualquer forma.

**Restrição registrada**: a API precisa rodar com um único processo uvicorn (é o que o `CMD` do Dockerfile já faz). Isso fica documentado no quickstart.

## R4. Extração de texto por formato (FR-003, FR-006, SC-008)

**Decision**: um módulo `backend/ingest/extract/` com uma função por formato e uma entrada única `extract(path, *, ocr=False) -> Extraction` (texto, páginas totais, páginas sem texto, avisos). Todos os extratores produzem o **mesmo formato de texto que o pipeline já espera**: parágrafos separados por linha em branco e títulos em linha própria, sem marcação `#`, porque o chunker detecta títulos por capitalização.

| Formato | Biblioteca | Observação |
|---------|-----------|------------|
| PDF | `pypdfium2` (Apache-2.0/BSD) | Rápido e com binário próprio. Também renderiza páginas para o OCR (R5). Arquivo com senha → `protected`. |
| EPUB | `zipfile` + `xml.etree` (stdlib) para `container.xml` → OPF → ordem do *spine*; XHTML via BeautifulSoup | `META-INF/encryption.xml` com algoritmo diferente da ofuscação de fontes (IDPF/Adobe) → `protected`. |
| DOCX | `python-docx` (MIT) | Parágrafos com estilo `Heading*`/`Title` viram linha de título. Tabelas são lidas célula a célula, como linhas. Arquivo que não é ZIP (DOCX com senha é um contêiner OLE) → `protected`. |
| HTML/HTM | `beautifulsoup4` (MIT) com `html.parser` | Remove `script`/`style`/`nav`. `h1`–`h6` viram linha de título. |
| ODT | `odfpy` | Mesmo mapeamento de títulos do DOCX. |
| RTF | `striprtf` (BSD) | Só texto. |
| TXT/MD | Leitura com `charset-normalizer` (já instalado de forma transitiva) para detectar a codificação | MD entra como está, como os livros atuais. |

**Validação de tipo**: extensão (lista fechada) e confirmação pelos bytes iniciais (`%PDF`, `PK` para EPUB/DOCX/ODT com o `mimetype` esperado, `{\rtf`). Um arquivo que não bate com a extensão vira `corrupt`.

**Rationale**: todas as bibliotecas têm licença permissiva e são Python puro ou wheels prontos para linux/arm64 e amd64. Nenhuma exige binário extra na imagem além do Tesseract (R5).

**Alternatives considered**:
- *PyMuPDF*: excelente, mas AGPL.
- *EbookLib*: AGPL, e a stdlib resolve o EPUB.
- *Pandoc* (cobre tudo): binário de mais de 100 MB e dependência de sistema a mais. A saída em Markdown também traria `#`, que o chunker não trata.
- *`unstructured`/`docling`*: pesados (modelos e torch), exagero para extrair texto de livros.

## R5. OCR de PDFs escaneados (FR-021 a FR-024, SC-009)

**Decision**:
- **Detecção**: uma página conta como "sem texto" se tiver menos de 40 caracteres alfabéticos extraídos. Se **≥ 80%** das páginas estiverem sem texto → `no_text` (o livro não é processado).
- **PDF parcialmente escaneado**: se **entre 20% e 80%** das páginas estiverem sem texto, o job também para em `no_text`, com `pages_without_text` preenchido, e oferece duas ações: "tentar com OCR" e "continuar sem OCR". Abaixo de 20%, o processamento segue direto e o resultado informa quantas páginas ficaram sem texto.
- **Motor**: Tesseract 5 (`tesseract-ocr` do Debian) via `pytesseract`, com páginas renderizadas pelo `pypdfium2` a 300 DPI. Idiomas instalados: `por`, `eng`, `fra`, `spa`, `ita`, que são os que o pipeline já detecta. Estratégia: OCR das 3 primeiras páginas com texto usando `por+eng`, detecção de idioma com o `_detect_lang` existente e OCR do resto com `<idioma>+eng`.
- **Paralelismo**: `ThreadPoolExecutor(max_workers=os.cpu_count())`. O `pytesseract` chama um subprocesso, então as threads não disputam o GIL. O progresso é gravado por página no job.
- Em PDF parcial, o OCR roda **só nas páginas sem texto**, e as demais usam o texto extraído.
- O texto do OCR passa pelos mesmos filtros de qualidade (`garbage_run_ratio`), conforme FR-024.

**Rationale para a regra de 20%**: a spec original oferecia OCR *depois* de o livro já ter sido adicionado, o que exigiria substituir trechos, ou seja, remover, e isso está fora do escopo (Clarifications, Q2). Decidir antes de indexar mantém a opção de OCR sem precisar remover nada. A spec foi ajustada no caso de borda correspondente.

**Estimativa**: cerca de 3 a 6 s por página por núcleo a 300 DPI. Com 8 núcleos, 300 páginas levam de 2 a 4 min; com 2 núcleos, de 8 a 15 min. Fica dentro dos 30 min do SC-009.

**Alternatives considered**:
- *OCRmyPDF*: robusto, mas traz Ghostscript e bem mais peso na imagem para um ganho pequeno aqui.
- *OCR por modelo de visão via OpenRouter*: melhor qualidade em layouts difíceis, mas o custo por página (300 páginas × imagem) é alto e depende da key.
- *EasyOCR/PaddleOCR*: exigem torch ou paddle, com imagem de vários GB.

**Impacto na imagem**: cerca de 40 a 60 MB (`tesseract-ocr` e cinco pacotes de idioma `-fast`/padrão).

## R6. Detecção de duplicatas (FR-008, SC-005)

> **Atualização (validação com livros reais):** a camada 2 abaixo (≥ 60% dos trechos com cosseno ≥ 0,97) **falhou**. A mesma obra extraída de PDF e de Markdown é cortada em trechos com fronteiras diferentes, e a semelhança por trecho fica entre 0,82 e 0,86 (Modernist Cuisine vol. 3, 4 e 6: só 1 a 3% dos trechos acima de 0,97). Com isso, 15 obras que já estavam no acervo entraram de novo. Substituída por duas etapas em `backend/ingest/dedup.py`: (1) os embeddings apontam até 3 livros candidatos, os que concentram ≥ 20% dos vizinhos mais próximos; (2) a verificação pelo texto mede a fração das sequências de 5 palavras do livro novo presentes no candidato, com corte em ≥ 15%. Medido: 26 a 99% para a mesma obra; 0 a 1,5% para obras diferentes, inclusive da mesma série. As 18 cópias antigas das 15 obras foram removidas com `backend/ingest/remove_books.py`, por decisão do usuário (manter as enviadas pela aba).

**Decision**: duas camadas.
1. **Arquivo idêntico**: o md5 do arquivo enviado é comparado com as chaves de `processed.json` (cobre os ~755 livros atuais) e com jobs já aceitos. Isso acontece antes de qualquer processamento, então não há custo.
2. **Mesmo conteúdo em outro arquivo ou formato**: depois de gerar os embeddings dos trechos novos (e antes da chamada de LLM para metadados), cada trecho é buscado no índice (k=1). Se **≥ 60% dos trechos** tiverem vizinho com **cosseno ≥ 0,97** e **mais da metade desses vizinhos pertencer a um mesmo livro**, o envio é marcado como `duplicate`, com `duplicate_of = <título>`.

**Rationale**: um hash do texto não pega a mesma obra em formatos diferentes, porque a extração de PDF e de EPUB produz texto ligeiramente diferente (quebras, hifenização, cabeçalhos). A proximidade de embeddings pega isso, funciona também para os livros antigos (não precisa de `books_md/`, que não existe aqui) e reaproveita embeddings que seriam gerados de qualquer forma. Fazer a checagem antes do LLM evita pagar metadados de uma duplicata.

**Riscos e calibragem**: duas edições diferentes do mesmo livro podem cair acima do limiar. Isso é aceitável, e o motivo mostrado cita o livro semelhante. Os limiares ficam como constantes em `backend/ingest/dedup.py` e são validados no quickstart (cenário Q7) com um livro real convertido em dois formatos.

**Alternatives considered**:
- *SimHash/MinHash do texto normalizado*: exige o texto limpo dos livros antigos, que não está disponível.
- *Comparar só o título detectado*: dá falso positivo com títulos genéricos como "Receitas" e falso negativo quando o título vem diferente.

## R7. Onde ficam os arquivos e a identidade do livro (FR-017, FR-025, FR-026)

**Decision**:
- **Staging**: `data/ingest/uploads/<job_id>/<nome original>`. O arquivo fica ali enquanto o job pode ser retomado (`queued`, `processing`, `no_text`, `error`).
- **Ao virar `added`**: o arquivo é movido para `books/<nome>`. Se já existir um arquivo com esse nome e conteúdo diferente, o nome ganha o sufixo ` (<md5[:6]>)` antes da extensão. O staging é apagado nos estados finais `added`, `duplicate` e `discarded`.
- **Slug único**: `_safe_slug(nome)`. Se já houver `data/index/metadata/<slug>.json` ou trechos com o prefixo `<slug>::`, acrescenta `-<md5[:6]>`. Isso evita que dois livros com o mesmo nome se sobrescrevam (caso de borda da spec).
- **Cache de texto extraído**: `data/ingest/text/<md5>.txt`, onde fica o texto final extraído (inclusive o obtido por OCR). O `build_index` consulta esse cache antes de extrair. Assim, um rebuild reprocessa um livro escaneado sem refazer o OCR, e um PDF com OCR em `books/` não é descartado como "sem texto" no rebuild (FR-025).
- **docker-compose**: `./books:/app/books` passa a ser volume, como `./data`.

**Rationale**: `books/` é a fonte que o CLI já lê, conforme a Clarification Q1. O staging fora de `books/` impede que um rebuild pegue arquivos ainda não aprovados.

## R8. Mudanças no pipeline de linha de comando (FR-025, FR-026)

**Decision**:
- `discover_and_dedupe` passa a listar todas as extensões suportadas (R4), não só `*.md`.
- `process_book` lê o texto via `extract()` (usando o cache do R7) em vez de `path.read_text`.
- Novo flag `--ocr`: PDFs sem texto e sem cache passam por OCR. Sem o flag, são pulados com aviso, porque o OCR de dezenas de livros pode levar horas.
- As etapas compartilhadas (extrair → limpar → classificar → dividir → filtrar lixo → metadados) viram funções reutilizadas pelo CLI e pelo worker, para que as regras de qualidade sejam idênticas (FR-006, FR-007).
- O CLI pega o lock de arquivo (R3) e executa a recuperação do diário (R2) ao iniciar.
- **Trava do `--rebuild`**: se `books/` tiver menos de 90% dos livros indexados em `processed.json`, o rebuild é recusado (a menos que se use `--force`). Neste checkout o acervo original não tem originais em `books/`, e sem a trava a Clarification Q1 tornaria um `--rebuild` destrutivo. Detalhes em [contracts/build-index-cli.md](contracts/build-index-cli.md).

## R9. Fila, estado e histórico persistentes (FR-013, FR-015, FR-019, US4, US5)

> **Atualização (implementação):** a fila foi para o **PostgreSQL 18**, a pedido do usuário. Serviço `db` no docker-compose, volume `pgdata`, driver `psycopg` 3 com pool de conexões, retirada da fila com `FOR UPDATE SKIP LOCKED` e migração automática do SQLite antigo na subida (`backend/ingest/migrate_sqlite.py`). Antes disso, um incidente mostrou a fragilidade do SQLite em WAL: aberto a partir do host com o container rodando, o arquivo `-shm` não é compartilhado através do bind mount e a API passou a não conseguir ler o banco. A decisão original está registrada abaixo.

**Decision**: SQLite (stdlib `sqlite3`) em `data/ingest/jobs.sqlite3`, com uma tabela `ingest_jobs` (ver [data-model.md](data-model.md)). O worker consome `status='queued' ORDER BY created_at`. Conexão por thread, `journal_mode=WAL`.

**Rationale**: um arquivo JSON teria corrida entre a thread do worker e as requisições HTTP. SQLite dá atomicidade, consulta por estado e histórico sem serviço extra, e fica em `data/`, que já persiste.

**Alternatives considered**: JSON com lock (frágil em escrita concorrente) e Redis (serviço a mais).

## R10. Contrato HTTP de upload e progresso (FR-002, FR-004, FR-010, FR-013)

**Decision**:
- Upload `multipart/form-data` em `POST /api/ingest/jobs` (`python-multipart`, que passa a ser dependência explícita). O arquivo é gravado em disco em blocos. Não há limite de tamanho (decisão do usuário).
- **Progresso por polling** (`GET /api/ingest/jobs`) a cada 1,5 s enquanto houver job ativo, e nenhum polling quando não houver.
- `GET /api/ingest/config` é a fonte única dos formatos aceitos, do limite de tamanho e da disponibilidade (key de embeddings e Tesseract instalado). O frontend valida antes do envio com esses dados (FR-004).
- Contrato completo em [contracts/ingest-api.md](contracts/ingest-api.md).

**Rationale**: o polling sobrevive a recarregar a página e a trocar de aba (US4 AC5) sem reconexão de stream, e o custo é desprezível para um usuário local. O SSE já usado em `/api/ask` faz sentido para tokens, não para um estado que muda a cada poucos segundos.

## R11. Aba sem chave de embeddings (item pendente do `/speckit-clarify`)

**Decision**: se `OPENROUTER_API_KEY` não estiver configurada, `GET /api/ingest/config` devolve `enabled=false` com `disabled_reason="embeddings_unavailable"`. A aba continua visível, mostra o aviso ("configure a chave do OpenRouter no `.env` para adicionar livros") e desabilita o envio. O histórico continua visível. Se o Tesseract não estiver instalado (execução fora do Docker), `ocr_available=false` e o botão "tentar com OCR" aparece desabilitado com a explicação.

## R12. Navegação entre abas no frontend (FR-001, FR-020)

**Decision**: estado de aba em `App.tsx`, sincronizado com o hash da URL (`#/consultar`, `#/adicionar`), sem biblioteca de rotas. A aba de consulta continua montada, apenas escondida, para não perder a resposta em andamento ao trocar de aba. Os textos novos entram em `frontend/src/lib/i18n.ts` (PT/EN). Após um job chegar a `added`, a aba chama `fetchStats()`/`fetchFilters()` de novo para atualizar o medidor e os filtros (FR-011).

**Alternatives considered**: `react-router` (dependência nova para duas telas).

## R13. Testes

**Decision**: `pytest` (nova dependência de desenvolvimento em `requirements-dev.txt`), em `tests/`:
- Unitários dos extratores, com fixtures pequenas versionadas em `tests/fixtures/` (um livro curto de receitas em PDF com texto, PDF escaneado, EPUB, EPUB com DRM simulado, DOCX, ODT, RTF, HTML, TXT em latin-1 e MD).
- Unitários da decisão de duplicata (R6) com vetores sintéticos.
- Integração do commit e da recuperação (R2), com um índice temporário pequeno e interrupção simulada entre as fases.
- Integração da API com `fastapi.testclient` e o embedding substituído por uma função determinística.

O frontend não tem suíte de testes hoje. A validação da aba é feita pelo [quickstart.md](quickstart.md) no navegador.
