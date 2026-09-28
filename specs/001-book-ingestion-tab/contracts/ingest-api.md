# Contract: API de Ingestão

Base: a mesma da API atual (`VITE_API_URL`, padrão `http://localhost:8000`). Todas as respostas são JSON. Os erros seguem o formato do FastAPI (`{"detail": ...}`), que o `getJSON` do frontend já trata.

Modelos em [../data-model.md](../data-model.md).

## Tipo `Job` (resposta)

```json
{
  "id": "3f2c9a…",
  "filename": "Cozinha Mineira.pdf",
  "format": "pdf",
  "size_bytes": 18432011,
  "status": "processing",
  "stage": "ocr",
  "progress": { "current": 57, "total": 300 },
  "reason_code": null,
  "reason_detail": null,
  "retryable": false,
  "actions": [],
  "pages_total": 300,
  "pages_without_text": 300,
  "result": null,
  "duplicate_of": null,
  "created_at": "2026-09-27T18:02:11Z",
  "updated_at": "2026-09-27T18:05:40Z",
  "finished_at": null
}
```

- `progress` é `null` quando o estágio não tem contagem.
- `result` é preenchido só em `added`: `{ "book_title": str, "lang": str, "n_chunks": int, "pages_without_text": int | null }`.
- `actions` lista as ações válidas agora, derivadas da máquina de estados: subconjunto de `["retry", "ocr", "continue_without_ocr", "cancel"]`. `cancel_requested: bool` indica um cancelamento pedido e ainda não atendido. O frontend mostra botões **só** a partir desse campo e não reimplementa a regra. `ocr` só aparece se `ocr_available`.

---

## `GET /api/ingest/config`

Disponibilidade e regras de validação no cliente (FR-004, R11).

**200**
```json
{
  "enabled": true,
  "disabled_reason": null,
  "ocr_available": true,
  "formats": [
    { "ext": "pdf",  "label": "PDF" },
    { "ext": "epub", "label": "EPUB" },
    { "ext": "docx", "label": "Word (DOCX)" },
    { "ext": "odt",  "label": "OpenDocument (ODT)" },
    { "ext": "rtf",  "label": "RTF" },
    { "ext": "html", "label": "HTML", "aliases": ["htm"] },
    { "ext": "txt",  "label": "Texto (TXT)" },
    { "ext": "md",   "label": "Markdown" }
  ]
}
```
`disabled_reason ∈ {null, "embeddings_unavailable"}`.

## `POST /api/ingest/jobs`

Envia um ou mais arquivos (FR-002). `multipart/form-data`, campo `files` repetido.

**201**
```json
{
  "jobs": [ Job, … ],
  "rejected": [ { "filename": "planilha.xlsx", "reason_code": "unsupported_format" } ]
}
```
- Cada arquivo válido vira um `Job`. Se o md5 for idêntico a um livro já no acervo, o job já nasce `duplicate`/`duplicate_file` e não entra na fila.
- `rejected` só aparece quando o cliente pulou a validação: `unsupported_format` (extensão fora da lista). Não há limite de tamanho.
- O frontend envia um arquivo por requisição, em sequência, para mostrar o progresso de cada envio e poder cancelá-lo.
- **409** `{"detail": "embeddings_unavailable"}` se `enabled=false`.
- **422** se nenhum arquivo for enviado.

## `GET /api/ingest/jobs`

Fila e histórico (FR-013, FR-019, US5). Ordem: mais recentes primeiro.

Query: `limit` (1–200, padrão 50), `before` (cursor ISO `created_at`, paginação), `active` (bool: só `queued`/`processing`).

**200** `{ "jobs": [ Job, … ], "has_active": bool, "next_before": str | null }`

O frontend faz polling a cada 1,5 s enquanto `has_active` for verdadeiro (R10).

## `GET /api/ingest/jobs/{id}`

**200** `Job` · **404** se não existir.

## `POST /api/ingest/jobs/{id}/retry`

Reprocessa sem reenviar (FR-015). Válido se `"retry" ∈ actions`.

**200** `Job` (agora `queued`) · **404** · **409** `{"detail": "invalid_transition"}`

## `POST /api/ingest/jobs/{id}/ocr`

Reprocessa com OCR (FR-022). Válido se `"ocr" ∈ actions`.

**200** `Job` (`queued`, `ocr_mode="ocr"`) · **404** · **409** `invalid_transition` · **409** `ocr_unavailable`

## `POST /api/ingest/jobs/{id}/cancel`

Cancela um job `queued`, `processing` ou `no_text` (FR-030). Válido se `"cancel" ∈ actions`.

**200** `Job` (`cancelled`, ou `processing` com `cancel_requested=true`) · **404** · **409** `invalid_transition` · **409** `too_late` (já gravando no acervo)

## `POST /api/ingest/jobs/{id}/continue-without-ocr`

Só para PDF parcial (`reason_code="partial_text"`): indexa com o texto disponível.

**200** `Job` (`queued`, `ocr_mode="skip_ocr"`) · **404** · **409** `invalid_transition`

---

## Endpoints existentes afetados

- `GET /api/stats` e `GET /api/filters`: sem mudança de forma. Passam a refletir livros adicionados sem reiniciar (FR-011) e recarregam o store se o índice for reconstruído por fora (R3).
- `GET /api/ask`: sem mudança. As buscas dividem o lock de leitura com o commit (R3).

## Fora do contrato (fora do escopo)

Não há `DELETE` de jobs nem de livros (Clarifications Q2), e não há edição de título (Clarifications Q3). Cancelar um job não remove nada do acervo, porque um job cancelado nunca chegou a gravar.
