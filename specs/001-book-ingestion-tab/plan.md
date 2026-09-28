# Implementation Plan: Aba de Ingestão de Livros

**Branch**: `001-book-ingestion-tab` | **Date**: 2026-09-27 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-book-ingestion-tab/spec.md`

## Summary

Nova aba "Adicionar livros" no frontend React, onde o usuário envia livros em PDF, EPUB, DOCX, ODT, RTF, HTML, TXT ou MD. O processamento roda em segundo plano, com estado por arquivo e histórico persistente, e o livro fica consultável sem reiniciar a aplicação.

Abordagem técnica ([research.md](research.md)):

- **Ingestão incremental dentro da API**: um worker em thread consome uma fila persistida em SQLite. Ele extrai o texto, reaproveita as etapas atuais de limpeza, classificação, chunking e metadados, gera embeddings só dos trechos novos e faz `add()` no `IndexFlatIP` em memória. O acervo existente não é reprocessado (R1).
- **Commit atômico com diário**: a gravação do índice e dos metadados passa por um diário de commit, com recuperação na subida. Uma interrupção nunca corrompe o acervo (R2).
- **Concorrência**: lock de leitura e escrita no `ChunkStore` e lock de arquivo com o CLI (R3).
- **Extratores por formato**: bibliotecas de licença permissiva, com saída no formato de texto que o chunker já espera (R4).
- **OCR opcional**: Tesseract, escolhido pelo usuário antes de indexar (R5).
- **Duplicatas em duas camadas**: md5 do arquivo e semelhança de embeddings. A segunda camada pega a mesma obra em outro formato (R6).
- **Originais em `books/`**: a pasta vira volume, e o CLI passa a ler todos os formatos, com cache de texto extraído e uma trava que impede o `--rebuild` de apagar o acervo original (R7, R8).

## Technical Context

**Language/Version**: Python 3.12 (backend, imagem `python:3.12-slim`) · TypeScript ~6.0 + React 19 (frontend, Vite 8)

**Primary Dependencies**: FastAPI, sse-starlette e faiss-cpu (existentes). Novas no backend: `python-multipart`, `pypdfium2`, `python-docx`, `beautifulsoup4`, `odfpy`, `striprtf`, `pytesseract` e `charset-normalizer` (esta já instalada de forma transitiva, passa a ser explícita). Novos pacotes de sistema: `tesseract-ocr` com os idiomas `por`, `eng`, `fra`, `spa` e `ita`. Frontend: nenhuma dependência nova.

**Storage**: arquivos em `data/index/` (FAISS, JSONL, JSON; formato mantido) + PostgreSQL 18 (serviço `db` do docker-compose, volume `pgdata`) para fila e histórico — trocado do SQLite durante a implementação, a pedido do usuário — + `books/` (novo volume) para os originais. Ver [data-model.md](data-model.md).

**Testing**: `pytest` (novo, `requirements-dev.txt`) com `fastapi.testclient`. O frontend é validado manualmente pelo [quickstart.md](quickstart.md), porque não há suíte de testes hoje.

**Target Platform**: containers Linux (arm64 e amd64) via docker-compose, com o frontend servido por nginx estático e chamando a API direto em `:8000`.

**Project Type**: web application (backend FastAPI + frontend React SPA), mais o pipeline de ingestão em linha de comando.

**Performance Goals**: livro com texto de ~300 páginas consultável em ≤ 5 min (SC-001). OCR de ~300 páginas em ≤ 30 min (SC-009). Tempo de ingestão com variação de no máximo 20% entre acervo vazio e acervo de ~750 livros (SC-002). Buscas não falham durante a ingestão (SC-004).

**Constraints**: uvicorn com um único processo (o worker vive nele). Nenhum commit parcial visível no acervo. Uploads sem limite de tamanho, gravados em disco por streaming. Sem autenticação (Clarifications, Q1 da spec). Sem remoção nem edição de livros (Clarifications Q2 e Q3). Embeddings exigem `OPENROUTER_API_KEY`.

**Scale/Scope**: acervo atual de ~580 livros indexados, 96.866 trechos e índice de ~595 MB. Uso pessoal, um usuário por vez, com lotes de dezenas de livros.

Nenhum item ficou como NEEDS CLARIFICATION. Todos foram resolvidos no [research.md](research.md).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

`.specify/memory/constitution.md` ainda é o **template não preenchido** (só placeholders `[PRINCIPLE_1_NAME]`, sem versão ratificada). Não há princípios nem gates para avaliar, e nada bloqueia.

Na falta de uma constituição, o plano segue as práticas já presentes no repositório e as registra aqui para a revisão pós-design:

| Prática observada no repo | Como o plano respeita |
|---------------------------|----------------------|
| As regras de qualidade do acervo ficam em um lugar só (`clean.py`, `classify.py` e os limiares de `garbage_run_ratio` compartilhados entre build e API) | O CLI e o worker usam as mesmas funções de etapa (R8). Nenhum limiar é duplicado. |
| O pipeline caro é retomável e não paga duas vezes (manifesto por md5) | O worker grava no mesmo `processed.json`. Duplicata exata é detectada antes de qualquer custo. O LLM roda depois da checagem de duplicata (R6). |
| `data/` é o artefato distribuído (baixado do Drive) e seu formato é estável | O formato de `data/index/` é mantido. Só há acréscimos: `data/ingest/`, `commit.json` e `.lock`. |
| Dependências mínimas no frontend | Nenhuma lib nova. Abas por hash (R12). |

**Re-check pós-design (Phase 1)**: continua sem violações. Recomendação: rodar `/speckit-constitution` antes do `/speckit-tasks` se quiser que essas práticas virem gates formais.

## Project Structure

### Documentation (this feature)

```text
specs/001-book-ingestion-tab/
├── plan.md               # este arquivo
├── research.md           # Phase 0 — decisões R1–R13
├── data-model.md         # Phase 1 — IngestJob, estados, armazenamento
├── quickstart.md         # Phase 1 — cenários de validação Q1–Q16
├── contracts/
│   ├── ingest-api.md     # endpoints /api/ingest/*
│   └── build-index-cli.md# mudanças no CLI de indexação
├── checklists/
│   └── requirements.md   # checklist de qualidade da spec
└── tasks.md              # Phase 2 (/speckit-tasks — ainda não criado)
```

### Source Code (repository root)

```text
backend/
├── Dockerfile                    # + tesseract-ocr e pacotes de idioma
├── api/
│   ├── main.py                   # + lifespan (recuperação, worker), recarga do store, include_router
│   └── ingest.py                 # NOVO — rotas /api/ingest/* (contracts/ingest-api.md)
├── ingest/
│   ├── build_index.py            # multi-formato, cache de texto, --ocr, --force, lock, trava do --rebuild
│   ├── pipeline.py               # NOVO — etapas compartilhadas CLI/worker (extrair→limpar→classificar→chunk→filtrar→metadados)
│   ├── formats.py                # NOVO — formatos aceitos, limite de tamanho, sniffing de bytes iniciais
│   ├── extract/                  # NOVO — um módulo por formato + extract() (R4)
│   │   ├── __init__.py
│   │   ├── pdf.py                # pypdfium2 + detecção de páginas sem texto
│   │   ├── ocr.py                # Tesseract via pytesseract, paralelo por página (R5)
│   │   ├── epub.py
│   │   ├── docx.py
│   │   ├── odt.py
│   │   ├── rtf.py
│   │   ├── html.py
│   │   └── text.py               # txt/md com detecção de encoding
│   ├── dedup.py                  # NOVO — camada de semelhança por embeddings (R6)
│   ├── jobs.py                   # NOVO — repositório SQLite de IngestJob + máquina de estados
│   ├── worker.py                 # NOVO — thread consumidora da fila
│   ├── commit.py                 # NOVO — commit com diário, recuperação, lock de arquivo (R2, R3)
│   └── metadata.py               # + campos opcionais origin/added_at em BookMeta
└── search/
    └── store.py                  # + RLock, append(), create_empty(), recarga por mtime

frontend/src/
├── App.tsx                       # + barra de abas com hash (#/consultar, #/adicionar)
├── App.module.css
├── components/
│   ├── IngestTab.tsx             # NOVO — tela da aba (aviso de indisponível, dropzone, lista)
│   ├── IngestTab.module.css
│   ├── UploadDropzone.tsx        # NOVO — seleção e arrastar-e-soltar, validação no cliente
│   ├── UploadDropzone.module.css
│   ├── JobList.tsx               # NOVO — fila e histórico, estados, motivos, ações
│   └── JobList.module.css
└── lib/
    ├── api.ts                    # + fetchIngestConfig, uploadBooks, fetchJobs, jobAction
    ├── types.ts                  # + IngestConfig, Job, JobStatus, ReasonCode
    └── i18n.ts                   # + textos da aba, estados e motivos (PT/EN)

tests/                            # NOVO
├── fixtures/                     # livros curtos em cada formato (R13)
├── unit/
│   ├── test_extract.py
│   ├── test_dedup.py
│   └── test_jobs_state_machine.py
└── integration/
    ├── test_commit_recovery.py
    ├── test_ingest_api.py
    └── test_build_index_cli.py

docker-compose.yml                # + volume ./books:/app/books
requirements.txt                  # + dependências novas (Technical Context)
requirements-dev.txt              # NOVO — pytest
README.md                         # + seção "Adicionar livros" e aviso sobre --rebuild
```

**Structure Decision**: estrutura web já existente (`backend/` + `frontend/`), sem reorganizar pastas. A lógica nova de ingestão fica em `backend/ingest/`, ao lado do pipeline que ela reaproveita. A camada HTTP fica num router separado (`backend/api/ingest.py`), para o `main.py` continuar pequeno. `tests/` é criado na raiz porque o repositório ainda não tem testes.

## Ordem de implementação sugerida

Cada passo entrega algo testável, e a ordem segue as prioridades da spec:

1. **Base do backend**: `formats.py`, `extract/` (sem OCR), `pipeline.py`, CLI multi-formato. Testes dos extratores. Isso já cobre FR-026.
2. **Índice incremental**: `store.py` (lock, append, create_empty) e `commit.py` (diário, recuperação, lock de arquivo). Testes de recuperação.
3. **Fila e API**: `jobs.py`, `worker.py`, `dedup.py`, `api/ingest.py` e lifespan. Testes da API. Aqui US1, US2 e US4 funcionam via HTTP.
4. **Frontend**: abas, `IngestTab`, `UploadDropzone`, `JobList`, i18n. Com isso, US1 a US5 funcionam na UI.
5. **OCR**: `extract/ocr.py`, ações `ocr`/`continue-without-ocr`, Dockerfile. Cobre FR-021 a FR-024.
6. **Operação**: volume `books/`, trava do `--rebuild`, README, quickstart completo.

## Riscos

| Risco | Mitigação |
|-------|-----------|
| O `--rebuild` passa a apagar o acervo original, porque os originais não estão em `books/` | Trava de 90% com `--force` explícito ([contracts/build-index-cli.md](contracts/build-index-cli.md)) |
| Falso positivo de duplicata entre edições diferentes da mesma obra | Limiares em constantes, calibrados no cenário Q7. O motivo exibido cita o livro semelhante. |
| O commit reescreve cerca de 800 MB por livro | Custo de segundos no volume atual. O índice segmentado fica registrado como evolução (R1). |
| Um OCR longo prende o worker e os demais envios esperam | Fila FIFO visível. O OCR só roda quando o usuário pede. O progresso por página mostra que o sistema não travou. |
| Os testes escrevem no acervo real | O quickstart exige backup de `data/`, e os testes automatizados usam índice temporário |

## Complexity Tracking

Não se aplica: não há constituição ratificada e, portanto, não há violações a justificar.
