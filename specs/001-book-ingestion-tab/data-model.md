# Data Model: Aba de Ingestão de Livros

**Feature**: [spec.md](spec.md) · **Research**: [research.md](research.md)

## Visão geral do armazenamento

```text
books/                                  # NOVO volume: originais de todos os livros (fonte do rebuild)
data/
├── index/                              # já existe: acervo consultável
│   ├── chunks.faiss                    # IndexFlatIP, recebe add() incremental
│   ├── chunks.jsonl                    # 1 Chunk por linha, mesma ordem dos vetores
│   ├── chunks_raw.jsonl                # entrada da fase 2 do CLI, recebe append
│   ├── manifest.json                   # resumo; mtime usado para detectar rebuild externo
│   ├── metadata/<slug>.json            # BookMeta, com 2 campos novos opcionais
│   ├── cache/processed.json            # md5 do original -> resultado (CLI e worker)
│   ├── commit.json                     # NOVO, transitório: diário do commit (R2)
│   └── .lock                           # NOVO: lock de arquivo entre API e CLI (R3)
└── ingest/                             # NOVO
    ├── jobs.sqlite3.migrated           # histórico antigo em SQLite, já migrado (backup)
    ├── uploads/<job_id>/<arquivo>      # staging enquanto o job é retomável
    └── text/<md5>.txt                  # cache do texto extraído (inclusive OCR)

PostgreSQL 18 (serviço "db", volume pgdata)
└── ingest_jobs                         # fila + histórico (IngestJob)
```

---

## IngestJob (novo, tabela `ingest_jobs` no PostgreSQL)

Tipos no Postgres: `size_bytes BIGINT`; `retryable` e `cancel_requested BOOLEAN`; datas `TIMESTAMPTZ` (a API as devolve em ISO-8601 UTC com `Z`); `CHECK` em `status`, `stage` e `ocr_mode`.

Representa um arquivo enviado pela aba (entidade "Envio" da spec). É a fonte da fila, do estado mostrado na aba e do histórico (FR-013, FR-019).

| Campo | Tipo | Regras |
|-------|------|--------|
| `id` | TEXT PK | uuid4 hex |
| `filename` | TEXT | Nome original, exibido na UI. Não vazio. |
| `format` | TEXT | Um de `pdf, epub, docx, odt, rtf, html, txt, md` (a extensão `.htm` é normalizada para `html`) |
| `size_bytes` | INTEGER | Tamanho do arquivo. Sem limite máximo. |
| `file_md5` | TEXT | md5 do arquivo original, indexado. Chave de duplicata exata. |
| `status` | TEXT | Ver a máquina de estados |
| `stage` | TEXT NULL | Estágio atual quando `processing` (ver a lista abaixo) |
| `progress_current` / `progress_total` | INTEGER NULL | Ex.: página 57/300 do OCR, lote 2/4 de embeddings |
| `reason_code` | TEXT NULL | Obrigatório em `duplicate`, `discarded`, `no_text` e `error` |
| `reason_detail` | TEXT NULL | Detalhe técnico curto (mensagem da exceção), para diagnóstico |
| `retryable` | INTEGER (bool) | Verdadeiro para `error` com `reason_code ∈ {embeddings_unavailable, interrupted, internal}` |
| `ocr_mode` | TEXT | `none` (padrão), `ocr`, `skip_ocr` (escolha do usuário em `no_text`) |
| `pages_total` / `pages_without_text` | INTEGER NULL | Preenchidos em PDFs |
| `book_title` | TEXT NULL | Título detectado, em `added` |
| `book_slug` | TEXT NULL | Slug único, em `added` |
| `lang` | TEXT NULL | `pt, en, fr, es, it`, em `added` |
| `n_chunks` | INTEGER NULL | Trechos indexados, em `added` |
| `duplicate_of` | TEXT NULL | Título do livro existente, em `duplicate` |
| `stored_path` | TEXT NULL | Caminho relativo em `books/` depois de `added` |
| `staged_path` | TEXT NULL | Arquivo no staging enquanto o job pode ser retomado |
| `cancel_requested` | INTEGER (bool) | Pedido de cancelamento de um job em `processing`, atendido pelo worker na próxima etapa |
| `created_at` / `updated_at` / `finished_at` | TEXT | ISO-8601 UTC |

Índices: `(status, created_at)` e `(file_md5)`.

### Máquina de estados

```text
                ┌──────────── retry (retryable) ─────────────┐
                ▼                                            │
 upload ──► queued ──► processing ──┬──► added               │
                ▲                   ├──► duplicate           │
                │                   ├──► discarded           │
                │                   ├──► error ──────────────┘
                │                   └──► no_text
                │                          │
                └── ocr / continue_without_ocr ┘
```

| De | Para | Gatilho |
|----|------|---------|
| — | `queued` | Upload aceito |
| — | `duplicate` | Upload com md5 idêntico a um livro existente (R6, camada 1). Não entra na fila. |
| `queued` | `processing` | O worker pega o job |
| `processing` | `added` | Commit concluído (R2) |
| `processing` | `duplicate` | Semelhança de conteúdo (R6, camada 2) |
| `processing` | `discarded` | `non_culinary`, `too_little_text` |
| `processing` | `no_text` | ≥ 20% das páginas sem texto e `ocr_mode='none'` (R5) |
| `processing` | `error` | Falha. `retryable` conforme o `reason_code`. |
| `processing` (na subida da API) | `error` | `reason_code=interrupted`, `retryable=1` |
| `no_text` | `queued` | Ação `ocr` (`ocr_mode='ocr'`) ou `continue_without_ocr` (`ocr_mode='skip_ocr'`, só se `pages_without_text < pages_total`) |
| `error` (retryable) | `queued` | Ação `retry` |
| `queued` / `no_text` | `cancelled` | Ação `cancel` (imediato; o staging é apagado) |
| `processing` | `cancelled` | Ação `cancel` marca `cancel_requested`; o worker para na próxima troca de etapa. Recusado (409 `too_late`) em `indexing`/`waiting_for_index_lock`. |
| `processing` com `cancel_requested` (na subida da API) | `cancelled` | — |

Estados finais: `added`, `duplicate`, `discarded`, `cancelled` e `error` com `retryable=0`. Nesses estados o staging é apagado. Nos estados retomáveis (`no_text`, `error` com `retryable=1`) o staging é mantido.

### `stage` (enquanto `processing`)

`waiting_for_index_lock` → `extracting` → `ocr` (só se `ocr_mode='ocr'`) → `cleaning` → `chunking` → `embedding` → `checking_duplicates` → `metadata` → `indexing`

### `reason_code`

| Código | Estado | Mensagem (PT) |
|--------|--------|---------------|
| `duplicate_file` | duplicate | "Este arquivo já está no acervo." |
| `duplicate_content` | duplicate | "Conteúdo muito parecido com «{duplicate_of}», que já está no acervo." |
| `non_culinary` | discarded | "Não parece ser um livro de gastronomia." |
| `reference_tome` | discarded | "Obra de referência extensa (estilo enciclopédia): o acervo não indexa esse tipo de livro." |
| `too_little_text` | discarded | "Sobrou pouco texto legível depois da limpeza." |
| `no_text` | no_text | "Sem texto extraível (PDF escaneado). Tente com OCR." |
| `partial_text` | no_text | "{n} de {total} páginas sem texto. Tente com OCR ou continue sem." |
| `protected` | error | "Arquivo protegido por senha ou DRM. Não é possível extrair o texto." |
| `corrupt` | error | "Arquivo corrompido ou com formato diferente da extensão." |
| `embeddings_unavailable` | error (retryable) | "Serviço de embeddings indisponível. Tente de novo mais tarde." |
| `interrupted` | error (retryable) | "O processamento foi interrompido. Tente de novo." |
| `internal` | error (retryable) | "Erro inesperado ao processar o arquivo." |

As mensagens ficam no frontend (`i18n.ts`, PT/EN), indexadas pelo código. A API devolve só o código e o `reason_detail`.

---

## Chunk (existente, `backend/search/store.py`, sem mudanças)

Os trechos dos livros novos usam exatamente a mesma estrutura: `id = "<slug>::<nnnn>"`, `book = BookMeta.title or slug`, e os demais campos vêm de `chunk_book` e `classify_book`. O slug único (R7) garante que os ids não colidam.

## BookMeta (existente, `backend/ingest/metadata.py`, dois campos opcionais novos)

| Campo novo | Tipo | Valor |
|------------|------|-------|
| `origin` | `"collection" \| "upload"` | Padrão `"collection"` (ausente nos 580 JSON atuais = acervo original) |
| `added_at` | `str \| None` | ISO-8601, só para `origin="upload"` |

É a "origem" pedida na entidade "Livro do acervo" da spec. Nenhum arquivo existente precisa ser migrado, porque a leitura usa `.get()` com o valor padrão.

## Entrada de `processed.json` (existente, formato mantido)

`{ "<md5 do original>": { "n_chunks": int, "tier": str, "slug": str } }`. O worker grava a mesma forma do CLI, para o CLI pular o livro. Duplicatas e descartes gravam `{"n_chunks": 0}`, como o CLI já faz com descartes.

## Diário de commit (`data/index/commit.json`, transitório)

`{ "job_id": str, "n_chunks_after": int, "chunk_ids": [str], "raw_append": bool }`. Existe só entre o passo 2 e o fim do passo 3 do R2. A presença dele na subida dispara a recuperação.

## Configuração de ingestão (derivada, sem persistência)

Exposta por `GET /api/ingest/config`: `enabled`, `disabled_reason`, `ocr_available`, `formats[]`. As constantes ficam em `backend/ingest/formats.py`.
