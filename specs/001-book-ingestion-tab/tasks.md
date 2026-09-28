---

description: "Task list for Aba de Ingestão de Livros"
---

# Tasks: Aba de Ingestão de Livros

**Input**: Design documents from `specs/001-book-ingestion-tab/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: incluídos, porque o [plan.md](plan.md) e o [research.md](research.md) (R13) definem uma suíte `pytest` e o quickstart depende dela. O frontend não tem testes automatizados e é validado pelo quickstart.

**Organization**: tarefas agrupadas por história de usuário, para cada uma poder ser implementada e testada de forma independente.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: pode rodar em paralelo (arquivos diferentes, sem dependência de tarefa incompleta)
- **[Story]**: história de usuário da [spec](spec.md) (US1–US5)
- Caminhos relativos à raiz do repositório. Estrutura web existente: `backend/` + `frontend/src/` + `tests/` (novo).

## Convenções que valem para todas as tarefas

- Comentários e mensagens de log em português sem acento, no estilo do código atual (`backend/ingest/*.py`).
- Texto produzido pelos extratores: parágrafos separados por uma linha em branco, títulos em linha própria **sem `#`** (o chunker detecta títulos por capitalização, ver research R4).
- Nenhum limiar de qualidade é duplicado: use sempre `clean_text`, `profile_book`, `chunk_book`, `garbage_run_ratio`, `GARBAGE_RATIO_DROP_CHUNK` e `classify_book` existentes.
- Todo acesso ao `ChunkStore` compartilhado passa pelo `StoreHolder` (T011).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: dependências e esqueleto de testes

- [X] T001 Adicionar a `requirements.txt` (nova seção `# --- ingestao multi-formato ---`): `python-multipart`, `pypdfium2`, `python-docx`, `beautifulsoup4`, `odfpy`, `striprtf`, `pytesseract`, `charset-normalizer`. Usar piso `>=` na versão estável atual de cada um, conferida com `pip index versions <pacote>`, e não fixar versões exatas, seguindo o padrão do arquivo.
- [X] T002 [P] Criar `requirements-dev.txt` com `-r requirements.txt` e `pytest>=8`.
- [X] T003 [P] Criar `tests/conftest.py` com as fixtures: `tmp_cfg` (instância de `backend.core.config.Config` com `books_raw`, `books_clean`, `index_dir` e `ingest_dir` apontando para `tmp_path`); `fake_embed` (monkeypatch de `backend.search.embed.embed_passages` e `embed_queries`, e dos nomes já importados em `backend.search.store`, `backend.ingest.worker` e `backend.ingest.build_index`, para um embedding determinístico de dimensão 64: hash sha256 dos trigramas do texto espalhado num vetor e normalizado L2, de modo que textos iguais dão vetores iguais e textos parecidos dão cosseno alto); `no_llm` (monkeypatch de `backend.ingest.metadata.classify_book` para sempre chamar com `use_llm=False`).
- [X] T004 [P] Criar `tests/fixtures/make_fixtures.py` com `build_all(out_dir: Path) -> dict[str, Path]`, exposto em `tests/conftest.py` como fixture de sessão `fixtures_dir`. Os arquivos são gerados em tempo de teste, sem binários versionados. Conteúdo base: um texto culinário em PT com pelo menos 3 receitas (título em Title Case, seção "Ingredientes" com quantidades como "200 g de farinha", seção "Modo de preparo"), com tamanho suficiente para passar `MIN_RAW_CHARS` e ser classificado como culinário por `profile_book`. Arquivos: `receitas.txt` (codificado em **latin-1**), `receitas.md`, `receitas.pdf` (PDF mínimo escrito à mão com fonte Helvetica e objetos de texto, um por página, com xref calculado), `escaneado.pdf` (3 páginas de imagem via `PIL.Image.save(..., "PDF", save_all=True)`), `parcial.pdf` (as páginas de `receitas.pdf` intercaladas com páginas de imagem, cerca de 50% sem texto, montado com `pypdfium2.PdfDocument.import_pages`), `receitas.epub` (zip com `mimetype`, `META-INF/container.xml`, OPF com spine de 2 capítulos XHTML), `drm.epub` (o mesmo, mais `META-INF/encryption.xml` com `EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc"`), `receitas.docx` (python-docx, títulos com estilo `Heading 1` e uma tabela de ingredientes), `protegido.docx` (bytes começando com o cabeçalho OLE `D0 CF 11 E0 A1 B1 1A E1`), `receitas.odt` (odfpy, `text:h` + `text:p`), `receitas.rtf`, `receitas.html` (com `<script>` e `<nav>` que devem sumir), `manual.txt` (texto técnico não culinário) e `quebrado.pdf` (os primeiros 300 bytes de `receitas.pdf`).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: infraestrutura de que todas as histórias dependem: formatos, extração base, pipeline compartilhado, índice incremental com commit seguro, fila persistente, rotas e abas vazias.

**⚠️ CRITICAL**: nenhuma história começa antes desta fase terminar.

- [X] T005 Em `backend/core/config.py`: adicionar o campo `ingest_dir: Path` (env `INGEST_DIR`, padrão `"data/ingest"`, via `_path`) e as properties `jobs_db_path` (`ingest_dir/"jobs.sqlite3"`), `uploads_dir` (`ingest_dir/"uploads"`), `text_cache_dir` (`ingest_dir/"text"`), `lock_path` (`index_dir/".lock"`), `commit_journal_path` (`index_dir/"commit.json"`), `processed_path` (`index_dir/"cache"/"processed.json"`), `chunks_raw_path` (`index_dir/"chunks_raw.jsonl"`) e `ingest_enabled` (`bool(self.openrouter_key.strip())`). Documentar `INGEST_DIR` em `.env.example` junto de `INDEX_DIR`.
- [X] T006 [P] Criar `backend/ingest/formats.py`: `SUPPORTED: dict[str, str]` com ext→label exatamente como em [contracts/ingest-api.md](contracts/ingest-api.md) (`pdf` "PDF", `epub` "EPUB", `docx` "Word (DOCX)", `odt` "OpenDocument (ODT)", `rtf` "RTF", `html` "HTML", `txt` "Texto (TXT)", `md` "Markdown"); `ALIASES = {"htm": "html"}`; `MAX_UPLOAD_BYTES = 1024 * 1024 * 1024` (era 200 MB; ver Assumptions da spec); `normalize_ext(filename) -> str | None` (minúsculas, aplica alias, `None` se não suportado); `sniff_matches(path, ext) -> bool` (`pdf`: começa com `%PDF`; `epub`/`odt`: zip cujo membro `mimetype` é `application/epub+zip` / `application/vnd.oasis.opendocument.text`; `docx`: zip com `[Content_Types].xml`; `rtf`: começa com `{\rtf`; `txt`/`md`/`html`: sempre `True`). Um DOCX com cabeçalho OLE `D0 CF 11 E0` retorna `True` aqui (a proteção é tratada no extrator). `config_payload(ocr_available: bool, enabled: bool) -> dict` no formato exato de `GET /api/ingest/config`, com `aliases: ["htm"]` no item html.
- [X] T007 [P] Criar `backend/ingest/extract/__init__.py`: `@dataclass Extraction(text: str, pages_total: int | None = None, pages_without_text: int | None = None, page_texts: list[str] | None = None, warnings: list[str] = [])` (usar `field(default_factory=list)`); `class ExtractionError(Exception)` com o atributo `reason_code` em `{"protected", "corrupt"}`; `extract(path: Path) -> Extraction` que escolhe o extrator por `normalize_ext` num dicionário `_EXTRACTORS` (registrados aqui: `txt` e `md`; os demais são registrados pelas histórias) e levanta `ExtractionError("corrupt")` se `sniff_matches` for falso; `cached_text(md5, cfg) -> str | None` e `store_text(md5, text, cfg)` em `cfg.text_cache_dir / f"{md5}.txt"` (escrita atômica: `.tmp` + `os.replace`).
- [X] T008 [P] Criar `backend/ingest/extract/text.py` (`txt`/`md`): `charset_normalizer.from_path(path).best()` → `str(result)`. Fallback: `path.read_text("utf-8", errors="replace")`. Registrar em `_EXTRACTORS` (T007).
- [X] T009 Em `backend/ingest/metadata.py`: adicionar a `BookMeta` os campos `origin: str = "collection"` (valores `"collection" | "upload"`) e `added_at: str | None = None`, ambos no fim para não quebrar a ordem posicional existente. `save_book_meta(meta, out_dir, stem: str | None = None)` usa `stem` quando informado, senão mantém o comportamento atual (`Path(meta.book).stem`).
- [X] T010 Criar `backend/ingest/pipeline.py` movendo de `build_index.py` `MIN_RAW_CHARS`, `_md5` (renomear para `file_md5`), `_safe_slug` (`safe_slug`) e `_detect_lang` (`detect_lang`), e expondo: `@dataclass Prepared(cleaned: str, tier: str, lang: str, raw_chunks: list[RawChunk])`; `prepare(text: str) -> Prepared | str`, que devolve o `reason_code` `"too_little_text"` (limpo < `MIN_RAW_CHARS` ou nenhum chunk depois do filtro de `garbage_run_ratio`) ou `"non_culinary"` (tier `TIER_NONCULINARY`); `unique_slug(filename, md5, cfg, taken: set[str]) -> str` (usa `safe_slug`; se existir `cfg.index_dir/"metadata"/f"{slug}.json"` ou o slug estiver em `taken`, retorna `f"{slug}-{md5[:6]}"`); `finalize(prepared, filename, slug, cfg, use_llm, origin="collection", added_at=None) -> tuple[BookMeta, list[Chunk]]`, que grava `cfg.books_clean/f"{slug}.md"`, chama `classify_book` com os primeiros 25 títulos de receita, preenche `origin`/`added_at`, grava com `save_book_meta(meta, cfg.index_dir/"metadata", stem=slug)` e monta os `Chunk` com `id=f"{slug}::{i:04d}"` e `book=meta.title or slug`, como hoje. Reescrever `process_book` em `backend/ingest/build_index.py` sobre `prepare` + `finalize`, mantendo o resultado idêntico para `.md` (mesmos ids, títulos e campos).
- [X] T011 Em `backend/search/store.py`: adicionar `self.lock = threading.RLock()` ao `ChunkStore` e envolver `search`, `stats`, `book_titles`, `regions` e `dish_type_options` com `with self.lock`. Adicionar `append(vectors: np.ndarray, chunks: list[Chunk])` (sob o lock: `index.add`, extensão de `chunks` e de `_folded`), `truncate(n: int)` (sob o lock: `index.remove_ids(faiss.IDSelectorRange(n, index.ntotal))` e corte das listas, usado para desfazer um append) e `@classmethod empty(dim) -> ChunkStore`. Criar `class StoreHolder` com `get() -> ChunkStore` (carrega na primeira chamada; `FileNotFoundError` continua subindo), `set(store)`, `get_or_none()` e o atributo `loaded_manifest_mtime: float | None`, com uma instância de módulo `HOLDER`.
- [X] T012 Criar `backend/ingest/commit.py` implementando o research R2 e R3: `index_lock(cfg, *, on_wait=None)`, context manager com `fcntl.flock(LOCK_EX)` em `cfg.lock_path` (tenta primeiro `LOCK_NB`; se falhar, chama `on_wait()` uma vez e bloqueia); `commit_book(holder, vectors, chunks, *, md5, processed_entry, job_id, cfg)`, que sob `index_lock`: (1) guarda `n_before`, faz `store.append`, grava `chunks.faiss.tmp` (`faiss.write_index`) e `chunks.jsonl.tmp` (cópia do atual + linhas novas), com `fsync` em cada um; se algo falhar até aqui, chama `store.truncate(n_before)`, apaga os `.tmp` e relança; (2) grava `cfg.commit_journal_path` com `{"job_id", "n_chunks_after", "chunk_ids", "md5", "processed_entry"}` e `fsync`; (3) chama `_apply_journal(cfg)`. `_apply_journal`: `os.replace` de cada `.tmp` que ainda existir; append em `cfg.chunks_raw_path` só dos ids de `chunk_ids` que ainda não estejam no fim do arquivo (verificar as últimas `len(chunk_ids)` linhas); `processed.json[md5] = processed_entry` (escrita `.tmp` + `os.replace`); reescrita de `manifest.json` com `{n_chunks, n_books, embed_model, built_at}` no formato de `phase2`; remoção do diário; atualização de `holder.loaded_manifest_mtime` com o mtime novo. `recover(cfg)`: com diário → `_apply_journal`; sem diário → apaga `chunks.faiss.tmp`/`chunks.jsonl.tmp` soltos. Se o store ainda não existir (acervo vazio), `commit_book` cria com `ChunkStore.empty(vectors.shape[1])` e faz `holder.set`. `record_processed(md5, entry, cfg)` grava só uma entrada em `processed.json` sob `index_lock` (usado por duplicatas e descartes).
- [X] T013 Criar `backend/ingest/jobs.py`: `JobRepo(cfg)` sobre SQLite em `cfg.jobs_db_path` (conexão por thread via `threading.local`, `PRAGMA journal_mode=WAL`, `CREATE TABLE IF NOT EXISTS ingest_jobs` com **todos** os campos de [data-model.md](data-model.md) → IngestJob, e índices `(status, created_at)` e `(file_md5)`). Constantes: `STATUSES = ("queued","processing","added","duplicate","discarded","no_text","error")`; `STAGES = ("waiting_for_index_lock","extracting","ocr","cleaning","chunking","embedding","checking_duplicates","metadata","indexing")`; `RETRYABLE_REASONS = {"embeddings_unavailable","interrupted","internal"}`; `ocr_mode` em `("none","ocr","skip_ocr")`, padrão `"none"`. A tabela de transições fica numa constante, exatamente como na seção "Máquina de estados" do data-model, e qualquer transição fora dela levanta `InvalidTransition`. Métodos: `create(...)`, `get(id)`, `list(limit=50, before=None, active=False)` (mais recentes primeiro), `claim_next()` (o `queued` mais antigo vira `processing` numa única transação), `set_stage(id, stage, current=None, total=None)`, `finish(id, status, **fields)` (grava `finished_at` em estados finais e `retryable=1` só se `status=="error"` e `reason_code in RETRYABLE_REASONS`), `request(id, action)` para `retry`/`ocr`/`continue_without_ocr`, `mark_interrupted()` (`processing` → `error`/`interrupted`/`retryable=1`) e `find_added_by_md5(md5)`. `to_api(job, ocr_available) -> dict` no formato exato do tipo `Job` de [contracts/ingest-api.md](contracts/ingest-api.md), incluindo `progress` (`null` sem contagem), `result` (só em `added`) e `actions` derivado: `retry` se `error` e `retryable`; `ocr` se `no_text` e `ocr_available`; `continue_without_ocr` se `no_text` e `reason_code=="partial_text"`. Datas em ISO-8601 UTC com `Z`.
- [X] T014 Criar `backend/api/ingest.py` com `router = APIRouter(prefix="/api/ingest")` (ainda sem rotas). Em `backend/api/main.py`: trocar `get_store` com `lru_cache` pelo `HOLDER` de T011 (`_store_or_503` usa `HOLDER.get()`); adicionar `lifespan` que chama `commit.recover(CONFIG)` e `JobRepo(CONFIG).mark_interrupted()` na subida; `app.include_router(ingest.router)` **antes** do `app.mount("/", StaticFiles...)`.
- [X] T015 [P] Em `frontend/src/lib/types.ts`: tipos `IngestFormat`, `IngestConfig`, `JobStatus` (`'queued'|'processing'|'added'|'duplicate'|'discarded'|'no_text'|'error'`), `JobStage` (os 9 estágios de T013), `ReasonCode` (os 11 códigos do data-model mais `'unsupported_format'|'too_large'`), `JobAction` (`'retry'|'ocr'|'continue_without_ocr'`), `Job`, `JobsPage` e `UploadResult`, espelhando [contracts/ingest-api.md](contracts/ingest-api.md). Em `frontend/src/lib/api.ts`: `fetchIngestConfig()`, `uploadBooks(files: File[])` (`FormData` com o campo `files` repetido, `POST /api/ingest/jobs`), `fetchJobs({limit, before, active})` e `jobAction(id, action)`, que mapeia `continue_without_ocr` → rota `continue-without-ocr`. Os erros seguem o padrão de `getJSON` (lançar `Error(detail)`).
- [X] T016 [P] Abas em `frontend/src/App.tsx` e `frontend/src/App.module.css`: estado `tab: 'consultar' | 'adicionar'` sincronizado com `location.hash` (`#/consultar` padrão, `#/adicionar`) e ouvindo `hashchange`; barra de abas no `header`, com os tokens visuais existentes em `App.module.css`/`index.css`; a área de consulta continua **montada** e só fica oculta (`hidden`) ao trocar de aba; `IngestTab` é renderizado na aba "adicionar" (criar `frontend/src/components/IngestTab.tsx` com um placeholder). Em `frontend/src/lib/i18n.ts`: `tabSearch` ("Consultar"/"Search") e `tabIngest` ("Adicionar livros"/"Add books").
- [X] T017 [P] Teste `tests/integration/test_commit_recovery.py` (usa `tmp_cfg`, vetores sintéticos e `Chunk` fictícios): commit normal → `ChunkStore.load` bate `ntotal == len(chunks)` e `processed.json`, `chunks_raw.jsonl` e `manifest.json` são atualizados; falha simulada (monkeypatch de `faiss.write_index` levantando exceção) → store em memória revertido, `.tmp` apagados e arquivos originais intactos; interrupção depois do diário e antes dos `os.replace` (monkeypatch de `_apply_journal` na primeira chamada) → `recover` conclui, e rodar `recover` de novo não duplica linhas em `chunks_raw.jsonl`; `.tmp` sem diário → `recover` apaga os `.tmp`; acervo vazio → `commit_book` cria o índice.
- [X] T018 [P] Teste `tests/unit/test_jobs_state_machine.py`: cada transição permitida do data-model passa; transições inválidas (por exemplo, `added`→`queued`, `retry` num `error` com `retryable=0`, `continue_without_ocr` com `reason_code="no_text"`) levantam `InvalidTransition`; `actions` derivado corretamente (inclusive `ocr` ausente com `ocr_available=False`); `mark_interrupted`; `claim_next` pega o mais antigo.
- [X] T019 [P] Teste `tests/unit/test_pipeline.py`: `prepare` sobre `receitas.md` devolve `Prepared` com tier culinário e chunks do tipo `receita`; sobre `manual.txt` devolve `"non_culinary"`; sobre um texto curto devolve `"too_little_text"`; `unique_slug` acrescenta `-<md5[:6]>` quando o slug já existe; `extract` de `receitas.txt` em latin-1 preserva "ç" e "ã".

**Checkpoint**: `pytest tests/ -q` passa, a API sobe com as rotas antigas intactas e a aba "Adicionar livros" aparece vazia.

---

## Phase 3: User Story 1 - Adicionar um livro à biblioteca e consultá-lo (Priority: P1) 🎯 MVP

**Goal**: enviar um livro (PDF com texto, TXT ou MD) pela aba, acompanhar o processamento e consultá-lo em seguida, sem reiniciar nada.

**Independent Test**: quickstart Q1, Q2, Q3 e Q14. Enviar `receitas.pdf`, ver "adicionado" com título, idioma e nº de trechos, e perguntar algo que só existe nele: a resposta cita o livro, e o medidor de livros sobe sem recarregar a página.

### Implementation for User Story 1

- [X] T020 [P] [US1] Criar `backend/ingest/extract/pdf.py`: abrir com `pypdfium2.PdfDocument`; `PdfiumError` com senha ou documento criptografado → `ExtractionError("protected")`, qualquer outra falha de abertura → `ExtractionError("corrupt")`; por página, `get_textpage().get_text_range()`; uma página conta como sem texto se tiver menos de `NO_TEXT_MIN_ALPHA = 40` caracteres alfabéticos; devolver `Extraction(text="\n\n".join(textos das páginas com texto), pages_total, pages_without_text, page_texts=[texto de cada página])`. Registrar em `_EXTRACTORS`. **Não** decidir aqui os limiares de 20%/80% (worker, T021 e T049).
- [X] T021 [US1] Criar `backend/ingest/worker.py`: `class IngestWorker(threading.Thread, daemon=True)` com `wake()` (`threading.Event`) e `stop()`; o loop chama `repo.claim_next()` e, sem job, espera o evento com timeout de 2 s. `process(job)`, com `set_stage` em cada etapa: `extracting` → `extract(staged_path)`; se `format=="pdf"` e `pages_without_text / pages_total >= 0.8` e `ocr_mode=="none"` → `finish(no_text, reason_code="no_text", pages_*)`; `cleaning`/`chunking` → `pipeline.prepare`, e se devolver código → `finish(discarded, reason_code=...)` e `record_processed(md5, {"n_chunks": 0})`; `embedding` → `embed_passages([f"{rc.title}\n{rc.text}" if rc.title else rc.text ...], cfg)`, com o mesmo formato de texto da `phase2`, em lotes de `EMBED_BATCH_CHUNKS`, atualizando `progress`; `metadata` → `pipeline.finalize(..., use_llm=True, origin="upload", added_at=agora)` com `unique_slug`; `indexing` → `commit_book(HOLDER, ...)` com `on_wait=lambda: set_stage("waiting_for_index_lock")` e `processed_entry={"n_chunks", "tier", "slug"}` → `finish(added, book_title, book_slug, lang, n_chunks, pages_*)`. Tratamento de erros: `ExtractionError` → `error` com o seu `reason_code`; `openai.APIConnectionError`, `APIStatusError` e o `RuntimeError` de `require_openrouter` durante embeddings → `error`/`embeddings_unavailable`; qualquer outra exceção → `error`/`internal` com `reason_detail=f"{type(e).__name__}: {e}"[:500]` e log do traceback. Um job com falha nunca derruba o loop.
- [X] T022 [US1] Em `backend/api/ingest.py`, implementar `GET /config` (`formats.config_payload(ocr_available=shutil.which("tesseract") is not None, enabled=CONFIG.ingest_enabled)`, com `disabled_reason="embeddings_unavailable"` quando desabilitado); `POST /jobs` (`files: list[UploadFile]`; **409** `{"detail": "embeddings_unavailable"}` se desabilitado; **422** sem arquivos; para cada arquivo: nome seguro = `Path(filename).name` sem caracteres de controle nem `/` e `\`, truncado em 200 caracteres (**nunca** usar o nome cru num caminho); extensão via `normalize_ext`, senão entra em `rejected` como `unsupported_format`; gravar em streaming, em blocos de 1 MiB, em `uploads_dir/<job_id>/<nome seguro>`, calculando md5 e, ao passar de `MAX_UPLOAD_BYTES`, abortar, apagar o diretório e rejeitar como `too_large`; criar o job `queued`; `worker.wake()`; resposta **201** `{"jobs": [...], "rejected": [...]}`); `GET /jobs` (`limit` 1–200, padrão 50, e `active: bool`; resposta `{"jobs", "has_active", "next_before": null}`, com a paginação vindo em US5); `GET /jobs/{id}` (404); `POST /jobs/{id}/retry` (404; 409 `{"detail":"invalid_transition"}`; em caso de sucesso, `wake()`).
- [X] T023 [US1] Em `backend/api/main.py`: iniciar o `IngestWorker` no `lifespan`, depois da recuperação de T014, e pará-lo no shutdown; expor o worker para o router, por exemplo em `app.state.ingest_worker`.
- [X] T024 [P] [US1] Criar `frontend/src/components/UploadDropzone.tsx` e `UploadDropzone.module.css`: `<input type="file" multiple accept=...>` montado a partir de `config.formats` (extensões e `aliases`) e área de arrastar-e-soltar com estado visual de arrasto; validação no cliente por extensão e por `max_file_bytes` **antes** do envio (FR-004), exibindo os arquivos recusados com o motivo e a lista de formatos aceitos; prop `disabled`; chamar `onFiles(validFiles)`. Acessível por teclado (a área é um `button`/`label` focável).
- [X] T025 [P] [US1] Criar `frontend/src/components/JobList.tsx` e `JobList.module.css`: uma linha por job, com nome do arquivo, formato, selo de estado (textos de `i18n`), estágio atual com `progress` "x/y" quando houver, resultado em `added` (título, idioma, nº de trechos e, se houver, "N páginas sem texto") e motivo traduzido por `reason_code` nos estados `duplicate`/`discarded`/`no_text`/`error` (substituindo `{duplicate_of}`, `{n}`, `{total}`); botões **somente** a partir de `job.actions` (`onAction(job.id, action)`).
- [X] T026 [US1] Implementar `frontend/src/components/IngestTab.tsx` (+ `IngestTab.module.css`): carregar `fetchIngestConfig()`; se `enabled=false`, mostrar o aviso "configure a chave do OpenRouter no .env para adicionar livros" e desabilitar o dropzone, mantendo a lista visível (R11); no envio, `uploadBooks` e mesclar os jobs retornados no topo; polling `fetchJobs({limit: 50})` a cada 1500 ms **enquanto** `has_active` for verdadeiro (parar quando não houver ativos, voltar após um novo envio ou ação, limpar no unmount); quando um job passar a `added`, chamar a prop `onLibraryChanged()`. Em `App.tsx`: `onLibraryChanged` busca `fetchStats()`/`fetchFilters()` de novo e ajusta `indexReady=true`.
- [X] T027 [US1] Em `frontend/src/lib/i18n.ts` (PT e EN): textos do dropzone, do aviso de indisponibilidade, os 7 estados, os 9 estágios, os 11 `reason_code` do data-model mais `unsupported_format` ("Formato não suportado. Aceitos: {formats}") e `too_large` ("Arquivo maior que {max}"), e as ações "Tentar de novo", "Tentar com OCR" e "Continuar sem OCR". Os textos em PT são os da tabela `reason_code` de [data-model.md](data-model.md).
- [X] T028 [US1] Teste `tests/integration/test_ingest_api.py` (`TestClient`, `tmp_cfg`, `fake_embed`, `no_llm`, `HOLDER` apontado para `tmp_cfg`): `GET /config` com e sem key; `POST /jobs` sem key → 409; com key, enviar `receitas.pdf` e `receitas.txt` → polling até `added` → `HOLDER.get().search("farinha")` devolve trechos desses livros e `GET /api/stats` conta os livros novos; `escaneado.pdf` → `no_text`; `manual.txt` → `discarded`/`non_culinary`; extensão `.xlsx` → `rejected`/`unsupported_format`; nome de arquivo `../../etc/passwd.txt` é gravado dentro de `uploads_dir`.

**Checkpoint**: MVP. Quickstart Q1, Q2, Q3 e Q14 passam.

---

## Phase 4: User Story 2 - Os livros adicionados sobrevivem a reinícios (Priority: P1)

**Goal**: originais em `books/` (volume), recuperação de interrupções, retry sem reenvio e pipeline de linha de comando compatível (FR-017, FR-018, FR-025, FR-026).

**Independent Test**: quickstart Q11, Q12, Q13 e Q16. Adicionar um livro, reiniciar ou reconstruir os containers e confirmar que ele continua consultável. Matar o backend durante o processamento: o acervo sobe íntegro e o job pode ser reprocessado.

### Implementation for User Story 2

- [X] T029 [US2] Em `docker-compose.yml`, no serviço `backend`: adicionar o volume `./books:/app/books`, com um comentário no estilo dos existentes (originais dos livros, fonte de um rebuild; ver o README).
- [X] T030 [US2] Em `backend/ingest/worker.py`: gravar sempre o texto extraído com `store_text(md5, text, cfg)` depois da extração; ao virar `added`, mover o original do staging para `cfg.books_raw/<nome>` (criar a pasta se preciso). Se `<nome>` já existir em `books/` com md5 diferente, usar `f"{stem} ({md5[:6]}){suffix}"`. Gravar `stored_path` relativo. Apagar `uploads_dir/<job_id>/` nos estados finais `added`, `duplicate`, `discarded` e em `error` com `retryable=0`, e mantê-lo em `no_text` e `error` retomável. O retry de um job cujo staging não existe mais vira `error`/`internal` com `reason_detail="arquivo original nao encontrado"` e `retryable=0`.
- [X] T031 [US2] Recarga após rebuild externo: em `backend/search/store.py`, `StoreHolder.reload_if_changed(cfg)` compara o mtime de `cfg.manifest_path` com `loaded_manifest_mtime` e, se mudou, carrega um novo `ChunkStore.load` e o troca atomicamente (atribuição da referência). Chamar em `_store_or_503` de `backend/api/main.py`. O commit do próprio worker já atualiza `loaded_manifest_mtime` (T012) e não dispara recarga.
- [X] T032 [US2] Em `backend/ingest/build_index.py` ([contracts/build-index-cli.md](contracts/build-index-cli.md)): `discover_and_dedupe` lista todos os arquivos de `cfg.books_raw` cujo `normalize_ext` não seja `None` (os demais continuam no aviso de ignorados, com o texto "formato nao suportado" em vez de "nao-markdown"); `process_book` obtém o texto via `cached_text(md5)` ou `extract(path)` + `store_text`; `ExtractionError` → conta como erro no manifesto, como hoje; PDF com ≥ 80% de páginas sem texto e sem cache → imprime `[ocr] <arquivo>: sem texto extraivel, pulado (use --ocr)` e **não** grava em `processed.json`; `main()` chama `commit.recover(cfg)` e segura `index_lock(cfg, on_wait=lambda: _log("[lock] aguardando a API terminar um commit..."))` durante a execução inteira.
- [X] T033 [US2] Trava do `--rebuild` em `backend/ingest/build_index.py`: novo flag `--force`; antes de apagar qualquer coisa com `--rebuild`, contar os arquivos suportados em `books/` e os livros com `n_chunks > 0` em `processed.json`; se a proporção for menor que 90% e não houver `--force`, sair com código 2 e a mensagem exata de [contracts/build-index-cli.md](contracts/build-index-cli.md) (seção "Trava de segurança do `--rebuild`"), com os números reais.
- [X] T034 [P] [US2] Teste `tests/integration/test_build_index_cli.py`: `books/` com `receitas.txt` e `receitas.pdf` → `main(["--no-llm"])` com `fake_embed` indexa os dois; um livro enviado pela aba (md5 já em `processed.json`) é pulado; `--rebuild` com `books/` quase vazio e `processed.json` com 10 livros → sai com código 2 sem tocar em `chunks_raw.jsonl`; com `--force`, prossegue; PDF escaneado sem cache → linha `[ocr] ... pulado` e ausência em `processed.json`.
- [X] T035 [P] [US2] Teste `tests/integration/test_persistence.py`: depois de um `added` via API, um `ChunkStore.load(tmp_cfg)` novo encontra os trechos e o original está em `books/`; um job deixado em `processing` → `mark_interrupted` na subida → `error`/`interrupted` com `actions == ["retry"]` → `POST /retry` → `added`, sem reenvio; dois uploads com o mesmo nome e conteúdos diferentes geram dois arquivos em `books/` e dois slugs distintos.

**Checkpoint**: US1 e US2 funcionam. Quickstart Q11, Q12, Q13 e Q16 passam.

---

## Phase 5: User Story 3 - Enviar livros em vários formatos (Priority: P2)

**Goal**: EPUB, DOCX, ODT, RTF e HTML, além de PDF, TXT e MD, e PDFs escaneados com OCR opcional (FR-003, FR-021 a FR-024).

**Independent Test**: quickstart Q4, Q5, Q9 e Q10. Enviar um livro curto em cada formato: todos ficam "adicionados" e consultáveis. `escaneado.pdf` → "Tentar com OCR" → adicionado. `parcial.pdf` → "Continuar sem OCR" → adicionado com páginas sem texto informadas.

### Implementation for User Story 3 — formatos

- [X] T036 [P] [US3] Criar `backend/ingest/extract/html.py`: `html_to_text(markup: str) -> str` com BeautifulSoup `html.parser`; remove `script`, `style`, `nav`, `header` e `footer`; `h1`–`h6` viram linhas de título isoladas por linhas em branco (sem `#`); `p`, `li`, `div` de bloco e `br` viram quebras de parágrafo; espaços colapsados. Registrar `html` com a leitura via `charset-normalizer`.
- [X] T037 [P] [US3] Criar `backend/ingest/extract/epub.py` (stdlib `zipfile` + `xml.etree.ElementTree`): `META-INF/container.xml` → `rootfile/@full-path` → OPF → `manifest` + `spine/itemref` em ordem → cada XHTML via `html_to_text` (T036). Se existir `META-INF/encryption.xml` com algum `EncryptionMethod/@Algorithm` fora de `{"http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC"}` → `ExtractionError("protected")`. `zipfile.BadZipFile` ou OPF ausente → `ExtractionError("corrupt")`.
- [X] T038 [P] [US3] Criar `backend/ingest/extract/docx.py`: arquivo começando com o cabeçalho OLE `D0 CF 11 E0 A1 B1 1A E1` → `ExtractionError("protected")`; `BadZipFile` → `corrupt`. Percorrer `document.element.body` na ordem, parágrafos e tabelas; parágrafos com estilo `Title` ou `Heading*` viram linha de título; tabelas viram uma linha por linha da tabela, com as células unidas por um espaço.
- [X] T039 [P] [US3] Criar `backend/ingest/extract/odt.py` com odfpy: `text:h` → linha de título; `text:p`/`text:list-item` → parágrafo via `odf.teletype.extractText`. Falha de leitura → `corrupt`.
- [X] T040 [P] [US3] Criar `backend/ingest/extract/rtf.py`: `striprtf.striprtf.rtf_to_text` sobre os bytes decodificados em `cp1252` (o fallback `latin-1` nunca falha).
- [X] T041 [US3] Registrar `epub`, `docx`, `odt`, `rtf` e `html` em `_EXTRACTORS` de `backend/ingest/extract/__init__.py`, e mostrar em `frontend/src/components/IngestTab.tsx` a linha "Formatos aceitos: …" com os `label` de `config.formats` (US3 AC3).
- [X] T042 [P] [US3] Teste `tests/unit/test_extract.py`: cada fixture de formato devolve texto contendo os títulos das receitas em linhas próprias, sem `#`, sem conteúdo de `<script>`/`<nav>`, e a tabela do DOCX aparece como linhas; `drm.epub` e `protegido.docx` → `protected`; `quebrado.pdf` e um `.epub` com bytes aleatórios → `corrupt`; `receitas.pdf` → `pages_without_text == 0`; `escaneado.pdf` → `pages_without_text == pages_total`.

### Implementation for User Story 3 — PDFs escaneados (OCR)

- [X] T043 [US3] Em `backend/Dockerfile`, no mesmo `RUN apt-get` do `libgomp1`: acrescentar `tesseract-ocr tesseract-ocr-por tesseract-ocr-eng tesseract-ocr-fra tesseract-ocr-spa tesseract-ocr-ita`, e atualizar o comentário.
- [X] T044 [US3] Criar `backend/ingest/extract/ocr.py`: `ocr_available() -> bool` (`shutil.which("tesseract")`); `ocr_pages(path, page_indexes: list[int], progress: Callable[[int, int], None]) -> dict[int, str]`: renderiza cada página com `pypdfium2` (`page.render(scale=300/72).to_pil()`); roda o OCR das 3 primeiras páginas da lista com `lang="por+eng"`, detecta o idioma com `pipeline.detect_lang` sobre esse texto, mapeia `{"pt":"por","en":"eng","fr":"fra","es":"spa","it":"ita"}` e faz o OCR do resto com `f"{codigo}+eng"` (ou só `"eng"` quando for inglês); `ThreadPoolExecutor(max_workers=os.cpu_count() or 2)`; chama `progress(feitas, total)` a cada página concluída. As 3 páginas iniciais contam no progresso e não são refeitas.
- [X] T045 [US3] Em `backend/ingest/worker.py`, regras de PDF do research R5: com `ratio = pages_without_text / pages_total`, `ratio >= 0.8` → `no_text`/`no_text`; `0.2 <= ratio < 0.8` → `no_text`/`partial_text`; `< 0.2` segue e informa as páginas em `result`. Isso vale só com `ocr_mode=="none"`. Com `ocr_mode=="ocr"`: estágio `ocr`, `ocr_pages` **só** nas páginas sem texto (progresso por página), junção dos textos de todas as páginas em ordem e processamento normal (o texto por OCR passa por `prepare`, conforme FR-024); `pages_without_text` passa a contar só as páginas que continuaram vazias depois do OCR. Com `ocr_mode=="skip_ocr"`: segue com o texto disponível. O cache de texto (T030) guarda o texto final, com OCR.
- [X] T046 [US3] Em `backend/api/ingest.py`: `POST /jobs/{id}/ocr` (404; 409 `invalid_transition`; 409 `{"detail":"ocr_unavailable"}` se `not ocr_available()`; em sucesso, `request(id, "ocr")` → `queued` com `ocr_mode="ocr"`, e `wake()`) e `POST /jobs/{id}/continue-without-ocr` (404; 409 `invalid_transition` se `reason_code != "partial_text"`; em sucesso, `ocr_mode="skip_ocr"`, `queued`, `wake()`).
- [X] T047 [US3] Em `backend/ingest/build_index.py`: flag `--ocr`. PDFs com ≥ 80% de páginas sem texto e sem cache passam por `ocr_pages` nas páginas vazias, com o texto salvo por `store_text`. Sem o flag, mantém o comportamento de T032.
- [X] T048 [US3] Frontend, em `frontend/src/components/JobList.tsx` e `IngestTab.tsx`: a ação `ocr` abre uma confirmação ("O OCR é bem mais lento, pode levar dezenas de minutos, e a qualidade depende da digitalização. Continuar?") antes de chamar `jobAction`; o estágio `ocr` mostra "página x/y"; quando `config.ocr_available=false` e o job estiver em `no_text`, mostrar o botão desabilitado com a explicação "OCR indisponível nesta instalação (Tesseract não encontrado)"; o motivo `partial_text` mostra "{n} de {total} páginas sem texto". Textos PT/EN em `frontend/src/lib/i18n.ts`.
- [X] T049 [P] [US3] Teste `tests/unit/test_pdf_rules.py` (monkeypatch de `ocr_pages` devolvendo o texto de `receitas.md` por página): `escaneado.pdf` → `no_text`/`no_text` com `actions` contendo `ocr` e não `continue_without_ocr`; `parcial.pdf` → `no_text`/`partial_text` com as duas ações; `continue-without-ocr` → `added` com `pages_without_text > 0`; `ocr` → `added` e `ocr_pages` chamado **só** com os índices das páginas sem texto; `POST /ocr` com `ocr_available=False` → 409 `ocr_unavailable`. Um teste com Tesseract real, marcado `@pytest.mark.skipif(not ocr_available())`, faz o OCR de `escaneado.pdf` (gerar as páginas com texto grande desenhado via `PIL.ImageDraw`).

**Checkpoint**: US1, US2 e US3 funcionam. Quickstart Q4, Q5, Q9 e Q10 passam.

---

## Phase 6: User Story 4 - Enviar vários livros de uma vez e entender o resultado de cada um (Priority: P2)

**Goal**: lotes, detecção de duplicatas nas duas camadas, motivo legível para cada arquivo recusado e processamento que independe da aba (FR-008, FR-012, FR-013).

**Independent Test**: quickstart Q6, Q7 e Q8. Um lote misto (válido, duplicata, não culinário, corrompido) termina com cada arquivo no estado certo e com motivo. `receitas.epub` depois de `receitas.pdf` → "já existe", citando o livro.

### Implementation for User Story 4

- [X] T050 [P] [US4] Criar `backend/ingest/dedup.py` (research R6): constantes `SIM_THRESHOLD = 0.97`, `MIN_MATCH_FRACTION = 0.6` e `MIN_DOMINANT_FRACTION = 0.5`; `find_duplicate(store: ChunkStore | None, vectors: np.ndarray) -> str | None`: devolve `None` com store vazio ou ausente; senão, sob `store.lock`, `store.index.search(vectors, 1)`, conta os trechos com score ≥ `SIM_THRESHOLD` e, se forem ≥ 60% do total e mais da metade desses vizinhos for do mesmo `chunk.book`, devolve esse título.
- [X] T051 [US4] Duplicata exata no upload, em `backend/api/ingest.py`: depois de gravar e calcular o md5, se `processed.json` tiver `md5` com `n_chunks > 0`, ou `repo.find_added_by_md5(md5)` existir, ou outro arquivo **do mesmo lote** tiver o mesmo md5, criar o job direto como `duplicate`/`duplicate_file` (com `duplicate_of` igual ao título, quando conhecido via o slug do manifesto → `metadata/<slug>.json`), apagar o staging e não acordar o worker.
- [X] T052 [US4] Em `backend/ingest/worker.py`: estágio `checking_duplicates` **depois** de `embedding` e **antes** de `metadata`; se `find_duplicate(HOLDER.get_or_none(), vetores)` achar um título → `finish(duplicate, reason_code="duplicate_content", duplicate_of=titulo)` e `record_processed(md5, {"n_chunks": 0})`, sem chamar o LLM.
- [X] T053 [US4] Frontend, em `frontend/src/components/IngestTab.tsx` e `JobList.tsx`: envio de vários arquivos numa única chamada (`uploadBooks(files)`) e aparição imediata de cada um na lista; itens `rejected` da resposta mostrados com o motivo; ação `retry` ligada a `jobAction`, com a lista atualizada e o polling retomado em seguida; ao voltar para a aba ou recarregar a página, o `fetchJobs` inicial restaura a fila e o histórico e retoma o polling se `has_active` (US4 AC5).
- [X] T054 [P] [US4] Teste `tests/unit/test_dedup.py` com vetores sintéticos: 100% de trechos iguais a um livro → título; 50% de trechos parecidos → `None`; 70% parecidos, mas espalhados por 3 livros sem maioria → `None`; store vazio → `None`.
- [X] T055 [P] [US4] Teste em `tests/integration/test_ingest_api.py`: o mesmo `receitas.txt` enviado duas vezes (em lotes diferentes e no mesmo lote) → o segundo é `duplicate`/`duplicate_file` sem passar por `processing`; `receitas.md` (mesmo conteúdo de `receitas.txt`, com `fake_embed`) → `duplicate_content`, com `classify_book` não chamado; lote com `receitas.docx`, `manual.txt` e `quebrado.pdf` → `added`, `discarded`/`non_culinary` e `error`/`corrupt` com `retryable=false`.

**Checkpoint**: US1 a US4 funcionam. Quickstart Q6, Q7 e Q8 passam.

---

## Phase 7: User Story 5 - Ver o histórico dos livros adicionados (Priority: P3)

**Goal**: histórico persistente e paginado dos envios, com data, título, formato, resultado e nº de trechos (FR-019).

**Independent Test**: quickstart Q11 (parte do histórico). Adicionar dois livros, reiniciar e ver os dois no histórico com os dados corretos.

### Implementation for User Story 5

- [X] T056 [US5] Paginação em `GET /api/ingest/jobs` (`backend/api/ingest.py` + `JobRepo.list`): parâmetro `before` (ISO `created_at`, exclusivo); `next_before` = `created_at` do último item quando vierem exatamente `limit` itens, senão `null`; `limit` validado entre 1 e 200 (422 fora da faixa).
- [X] T057 [US5] Em `frontend/src/components/JobList.tsx`: separar "Em andamento" (`queued`/`processing`) de "Histórico" (demais); no histórico, mostrar a data de envio formatada com `Intl.DateTimeFormat` no idioma ativo, nome do arquivo, formato, título detectado, resultado e nº de trechos; botão "Carregar mais", que chama `fetchJobs({before: next_before})` e concatena; estado vazio "Nenhum livro adicionado por aqui ainda". Textos PT/EN em `i18n.ts`.
- [X] T058 [P] [US5] Teste em `tests/integration/test_ingest_api.py`: com 7 jobs e `limit=3`, três páginas com `next_before` encadeado e a última com `next_before=null`; `limit=0` → 422.

**Checkpoint**: as 5 histórias funcionam de forma independente.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T059 [P] Em `README.md`: seção "Adding books" com a aba, os formatos, o OCR opcional, o fato de os originais ficarem em `books/` (volume) e o texto e os índices em `data/`, e a exigência da chave do OpenRouter para embeddings. Seção "Rebuilding the index" explicando a trava do `--rebuild`/`--force` e o `--ocr`. Nota de desenvolvimento: `brew install tesseract tesseract-lang` e uvicorn **sem** `--workers`. No mesmo estilo e idioma (inglês) do README atual.
- [X] T060 [P] Em `frontend/src/App.tsx`: no painel de acervo não encontrado (`indexReady === false`), corrigir o comando para `python -m backend.ingest.build_index` e acrescentar um link para `#/adicionar` ("ou adicione livros pela aba Adicionar livros"), já que a ingestão cria o índice do zero. Textos em `i18n.ts`.
- [X] T061 Conferir o volume `books/` e o `.gitignore`: `books/` e `data/` já são ignorados; garantir que `data/ingest/` também não é versionado (já coberto por `data/`) e que `docker compose up` cria `./books` quando ele não existe.
- [X] T062 Rodar `pytest tests/ -q`, `cd frontend && npm run lint && npm run build` e corrigir o que falhar.
- [ ] T063 (parcial — ver nota) Executar os cenários Q1–Q16 e a verificação do SC-002 do [quickstart.md](quickstart.md), com backup de `data/` antes, e registrar os tempos medidos de SC-001, SC-002 e SC-009. Ajustar `SIM_THRESHOLD`/`MIN_MATCH_FRACTION` em `backend/ingest/dedup.py` se o Q7 falhar com livros reais.
  - **Feito com livros reais**: Q1/Q2 (*Le Cordon Bleu – Sobremesas*, 459 trechos, e *Modernist Cuisine at Home*, 712 trechos, consultáveis); Q9 (OCR de 222 páginas em ~3 min 20 s no container, SC-009 ✓); Q10 (*Modernist Cuisine nº 2*: 205 de 487 páginas sem texto → `partial_text`); Q12 (várias reconstruções da imagem sem perda); Q13 (interrupção real durante OCR → `interrupted` → retry → adicionado); Q16; Q17–Q20; roteiro de navegador (Q5, Q6, Q15, Q17–Q19, 390 px).
  - **Falta**: Q7 com a mesma obra em PDF e EPUB reais (calibrar a duplicata por conteúdo); Q3 medido no container (buscas durante uma ingestão); medição do SC-002 (acervo vazio × completo).

---

## Phase 9: Mudanças pedidas ou descobertas durante a implementação

- [X] T064 [US4] Envio de pasta inteira (seleção com `webkitdirectory` e arraste de pastas percorrendo subpastas; ocultos ignorados; formatos não suportados resumidos numa mensagem) em `frontend/src/components/UploadDropzone.tsx` e `IngestTab.tsx` (FR-028)
- [X] T065 [US4] Envio sequencial por arquivo via XHR, com progresso e **Cancelar envio**, em `frontend/src/lib/api.ts` e `IngestTab.tsx` (FR-029)
- [X] T066 [US4] Cancelamento de jobs `queued`/`processing`/`no_text`: estado `cancelled`, `cancel_requested`, troca de etapa atômica com o cancelamento, recusa em `indexing`, `POST /jobs/{id}/cancel`, botão na lista, em `backend/ingest/jobs.py`, `worker.py`, `backend/api/ingest.py`, `JobList.tsx`; testes em `tests/integration/test_cancel.py` (FR-030)
- [X] T067 Remover o limite de tamanho por arquivo (backend, frontend, contrato, README) (FR-004)
- [X] T068 Motivo próprio `reference_tome` para o descarte por "tomo de referência" e `too_little_text` para livro ilegível, em `backend/ingest/classify.py` e `pipeline.py` (FR-007)
- [X] T069 Extração de PDF: texto do `get_text_range` + posição dos caracteres para quebrar parágrafos (a versão por retângulos duplicava letras em PDFs reais) em `backend/ingest/extract/pdf.py`
- [X] T070 OCR: renderização numa thread só (pdfium não é thread-safe) e Tesseract chamado direto com `OMP_THREAD_LIMIT=1` (de ~23 s para ~0,9 s por página no container); `pytesseract` removido, em `backend/ingest/extract/ocr.py`
- [X] T071 Rótulo distinto para livros diferentes com o mesmo título (`distinct_label`) no worker e no CLI
- [X] T073 A pedido do usuário: fila/histórico no **PostgreSQL 18** (serviço `db` no `docker-compose.yml`, volume `pgdata`, `psycopg` 3 + pool, `FOR UPDATE SKIP LOCKED`), migração automática e idempotente do SQLite (`backend/ingest/migrate_sqlite.py`), testes com schema isolado por teste (`tests/conftest.py`, `tests/integration/test_postgres_migration.py`)
- [X] T074 Recuperação de livro gravado no acervo mas não marcado como adicionado (queda durante a cópia do original para `books/`): `IngestWorker.recover()` na subida da API
- [X] T075 Duplicata por conteúdo em duas etapas (candidatos por embeddings + confirmação pelas sequências de 5 palavras), depois que a regra só por embeddings deixou passar 15 obras reais, em `backend/ingest/dedup.py`; testes em `tests/unit/test_dedup.py`
- [X] T076 Falta de memória (backend morto 3 vezes pelo kernel da VM do Docker): OCR limitado a `OCR_WORKERS=2`, sem recarga do índice durante um commit, e recarga que solta a cópia antiga antes de carregar a nova, em `backend/ingest/extract/ocr.py`, `commit.py` e `backend/search/store.py`
- [X] T077 Ferramenta de manutenção `backend/ingest/remove_books.py` (remover livros com o backend parado, limpar rótulos "[arquivo]", marcar em `processed.json`, atualizar o histórico); usada para tirar as 18 cópias antigas das 15 obras duplicadas; testes em `tests/integration/test_remove_books.py`
- [X] T078 Fora do escopo original, a pedido do usuário: agente com um prompt só (`backend/prompts/chef_system.md`; `chef_fallback.md` removido), busca na web como ferramenta (`web_search`/Tavily) nos 4 provedores com buscas paralelas (`backend/core/providers.py`), citações da web numeradas depois dos trechos, memória curta efêmera por conversa (`backend/agent/memory.py`) com reescrita da pergunta de continuação para a busca, botões "Copiar resposta" e "Nova conversa"; testes em `tests/unit/test_agent.py` e `tests/integration/test_ask_api.py`
- [X] T079 Lentidão do agente: `/api/ask` bloqueava o event loop (busca e streaming síncronos dentro de rota async), e a resposta chegava inteira só no fim (8–20 s de tela vazia); agora em threadpool, com streaming de verdade. Índice pré-carregado na subida (a 1ª pergunta esperava ~30 s)
- [X] T072 Fora do escopo original, a pedido do usuário: busca híbrida (semântica + termos raros por RRF) em `backend/search/store.py` e regra de montar a resposta a partir de vários trechos em `backend/prompts/chef_system.md`; testes em `tests/unit/test_hybrid_search.py`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: sem dependências.
- **Foundational (Phase 2)**: depende do Setup e **bloqueia todas as histórias**. Ordem interna: T005 → (T006, T007, T008, T009 em paralelo) → T010 → T011 → T012 → T013 → T014. T015 e T016 (frontend) correm em paralelo com todo o backend. T017–T019 vêm depois do que testam.
- **US1 (Phase 3)**: depende da Foundational.
- **US2 (Phase 4)**: depende da US1 (usa o worker e o fluxo de upload).
- **US3 (Phase 5)**: depende só da US1. A parte de formatos (T036–T042) só precisa da Foundational e pode começar junto com a US1.
- **US4 (Phase 6)**: depende da US1.
- **US5 (Phase 7)**: depende da US1.
- **Polish (Phase 8)**: depois das histórias desejadas.

### User Story Dependencies

```text
Setup ─► Foundational ─► US1 (MVP) ─┬─► US2
                                    ├─► US3 ── (formatos T036–T042 podem começar já após Foundational)
                                    ├─► US4
                                    └─► US5
```

US2 a US5 não dependem entre si, mas tocam arquivos em comum (`worker.py`, `api/ingest.py`, `JobList.tsx`, `i18n.ts`). Em paralelo, coordene as edições nesses arquivos.

### Within Each User Story

- Backend (extrator, worker, rotas) antes do frontend que o consome.
- Testes da história ao final da história (a suíte depende das fixtures de T004 e do `conftest` de T003).

---

## Parallel Opportunities

- **Setup**: T002, T003 e T004 juntos.
- **Foundational**: T006, T007, T008 e T009 juntos; T015 e T016 (frontend) em paralelo com T010–T014; T017, T018 e T019 juntos.
- **US1**: T020 (extrator de PDF) em paralelo com T024 e T025 (componentes de frontend).
- **US3**: T036–T040 (cinco extratores, arquivos diferentes) todos juntos; T042 e T049 juntos.
- **US4**: T050 e T054 juntos.
- **Entre histórias**: depois do MVP, US3 (extratores), US4 (`dedup.py`) e US5 (paginação) podem andar em paralelo, observando os arquivos compartilhados.

## Parallel Example: User Story 3

```bash
Task: "Criar backend/ingest/extract/html.py (T036)"
Task: "Criar backend/ingest/extract/epub.py (T037)"   # usa html_to_text: começar depois da assinatura de T036
Task: "Criar backend/ingest/extract/docx.py (T038)"
Task: "Criar backend/ingest/extract/odt.py (T039)"
Task: "Criar backend/ingest/extract/rtf.py (T040)"
```

## Parallel Example: User Story 1

```bash
Task: "Criar backend/ingest/extract/pdf.py (T020)"
Task: "Criar frontend/src/components/UploadDropzone.tsx (T024)"
Task: "Criar frontend/src/components/JobList.tsx (T025)"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1 (Setup) + Phase 2 (Foundational). O commit seguro (T012) fica **dentro** da fundação de propósito: mesmo no MVP, o índice real de 600 MB nunca é gravado sem o diário.
2. Phase 3 (US1).
3. **PARAR E VALIDAR**: quickstart Q1, Q2, Q3 e Q14, com backup de `data/`.

Com isso, já é possível adicionar PDF, TXT e MD pela aba e consultá-los. Os originais ainda ficam no staging `data/ingest/uploads/`, que já persiste porque `data/` é volume, mas só passam a ser vistos pelo rebuild do CLI na US2.

### Incremental Delivery

1. MVP (US1) → demo.
2. US2: `books/` como volume, CLI multi-formato e trava do rebuild. Recomendado logo em seguida, porque fecha a exigência explícita de persistência e o risco do `--rebuild`.
3. US3: formatos restantes + OCR.
4. US4: lotes e duplicatas.
5. US5: histórico paginado.
6. Polish.

---

## Notes

- [P] = arquivos diferentes, sem dependência de tarefa incompleta.
- Remoção e edição de livros estão fora do escopo (Clarifications Q2 e Q3): nenhuma tarefa cria `DELETE` ou edição de título.
- Faça commit a cada tarefa ou grupo lógico, e pare em cada checkpoint para validar a história isoladamente.
- Antes de rodar qualquer cenário manual que escreva no acervo real, faça backup de `data/`.
