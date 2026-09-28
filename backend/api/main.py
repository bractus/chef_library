"""API leve sobre o pipeline backend/* — serve o frontend novo (React).

Não reimplementa busca nem geração: so encapsula ChunkStore.search e
agent.ask_stream/build_context em endpoints HTTP + streaming (SSE).

Rodar em dev:
    uvicorn backend.api.main:app --reload --port 8000
"""
from __future__ import annotations

import json
import threading
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

from ..agent import MEMORY, ask_stream, standalone_query
from ..core.config import CONFIG, Config
from ..ingest import commit
from ..ingest.clean import GARBAGE_RATIO_FLAG, garbage_run_ratio
from ..ingest.jobs import JobRepo
from ..ingest.migrate_sqlite import migrate_sqlite
from ..ingest.worker import IngestWorker
from ..search.store import HOLDER, ChunkStore, Hit, SearchFilters
from . import ingest


@asynccontextmanager
async def lifespan(app: FastAPI):
    # testes trocam a config via app.state.cfg antes de subir o app
    cfg: Config = getattr(app.state, "cfg", CONFIG)
    HOLDER.configure(cfg)
    commit.recover(cfg)  # conclui/descarta um commit interrompido antes de carregar o indice
    repo = JobRepo(cfg)
    migrate_sqlite(cfg, repo)  # historico de antes do Postgres, uma vez so
    worker = IngestWorker(cfg, repo, HOLDER)
    worker.recover()  # livros ja gravados no acervo antes de uma queda
    repo.mark_interrupted()
    app.state.ingest = ingest.IngestContext(cfg=cfg, repo=repo, worker=worker, holder=HOLDER)
    worker.start()
    # carrega o indice (~600 MB + 100 mil trechos, 15-30 s) ja na subida, em
    # segundo plano — sem isso a primeira pergunta pagava a carga inteira
    threading.Thread(target=HOLDER.get_or_none, name="preload-index", daemon=True).start()
    try:
        yield
    finally:
        worker.stop()
        worker.join(timeout=5)
        repo.close()


app = FastAPI(title="Chef Library API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

KINDS = ["receita", "tecnica", "texto"]


app.include_router(ingest.router)


def _store_or_503() -> ChunkStore:
    try:
        HOLDER.reload_if_changed()  # indice reconstruido pelo CLI por fora
        return HOLDER.get()
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


def _hit_to_dict(h: Hit) -> dict:
    d = asdict(h)
    # marca trechos cujo OCR esta ilegivel demais para confiar — mesma
    # heuristica usada no build (backend/ingest/clean.py) para descartar chunks,
    # aqui exposta como sinal na UI em vez de descartar silenciosamente.
    d["low_quality"] = garbage_run_ratio(h.text) > GARBAGE_RATIO_FLAG
    return d


@app.get("/api/stats")
def stats() -> dict:
    return _store_or_503().stats()


@app.get("/api/filters")
def filters() -> dict:
    store = _store_or_503()
    return {
        "regions": store.regions(),
        "dish_types": store.dish_type_options(),
        "books": store.book_titles(),
        "kinds": KINDS,
        "agent_model": CONFIG.openrouter_model,
        "embed_model": CONFIG.embed_model,
    }


@app.get("/api/ask")
async def ask(
    request: Request,
    q: str = Query(..., min_length=1),
    k: int = Query(8, ge=1, le=20),
    lang: Literal["pt", "en"] = "pt",
    kind: Optional[str] = None,
    ingredient: Optional[str] = None,
    book: Optional[str] = None,
    region: Optional[str] = None,
    dish_type: Optional[str] = None,
    conversation_id: Optional[str] = Query(None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"),
):
    store = _store_or_503()
    cfg: Config = request.app.state.ingest.cfg

    filters = SearchFilters.from_request(kind, ingredient, book, region, dish_type)
    history = MEMORY.history(conversation_id)
    # chamadas de rede (reescrita da pergunta, embedding, LLM) fora do event loop
    search_q = await run_in_threadpool(standalone_query, q, history, cfg)
    hits = await run_in_threadpool(store.search, search_q, k, filters)

    async def events():
        # sem trechos no acervo a resposta vem da web/conhecimento do modelo;
        # "fallback" so avisa a interface para sinalizar isso
        if hits:
            yield {"event": "sources", "data": json.dumps([_hit_to_dict(h) for h in hits], ensure_ascii=False)}
        else:
            yield {"event": "fallback", "data": "{}"}

        answer: list[str] = []
        stream = ask_stream(q, hits, history, lang=lang, filters=filters.describe(), cfg=cfg)
        try:
            async for kind_, payload in iterate_in_threadpool(stream):
                if kind_ == "token":
                    answer.append(payload)
                    yield {"event": "token", "data": payload}
                elif kind_ == "web_sources":
                    yield {"event": "web_sources", "data": json.dumps(payload, ensure_ascii=False)}
                elif kind_ == "searching":
                    yield {"event": "searching", "data": json.dumps({"query": payload}, ensure_ascii=False)}
        except RuntimeError as e:
            yield {"event": "error", "data": str(e)}
            return
        except Exception as e:
            yield {"event": "error", "data": f"Erro ao chamar o agente: {e}"}
            return

        MEMORY.add(conversation_id, q, "".join(answer))
        yield {"event": "done", "data": json.dumps({"turns": len(MEMORY.history(conversation_id))})}

    return EventSourceResponse(events())


@app.delete("/api/conversations/{conversation_id}", status_code=204)
def forget_conversation(conversation_id: str) -> None:
    """"Nova conversa" na interface: esquece a memoria curta dessa conversa."""
    MEMORY.forget(conversation_id)


_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="frontend")
